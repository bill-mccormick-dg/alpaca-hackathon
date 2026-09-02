"""override.py - the CLI republishes config/effective after a change (#130).

At 09:53 ET on 2026-08-31 the official account was switched to Kimi-K2.6
from the CLI; the override applied, but the HA model select kept showing
K2-Instruct for up to ten minutes, because only the bridge and the cycle's
`config` event ever published the retained topic. The runtime was never
wrong - only the display was. These tests pin that a CLI set/clear now
refreshes the topic with the effective config, and that a broker failure
changes nothing about the override itself.
"""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import override
from bot import mqtt, overrides
from bot.risk import EASTERN

NOW = datetime(2026, 9, 1, 10, 30, tzinfo=EASTERN)  # Tue, mid-session


class Capture:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, topic, payload, retain):
        if self.fail:
            raise ConnectionError("broker down")
        self.calls.append((topic, payload, retain))


class RepublishEffectiveTest(unittest.TestCase):
    def setUp(self):
        self.cap = Capture()
        patcher = mock.patch.object(mqtt, "_publisher", self.cap)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: mqtt.configure({}, "test"))
        # The overrides file goes to a temp dir so the test never touches
        # logs/; load_config() then merges from that same file.
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        p = mock.patch.object(overrides, "OVERRIDES_FILE", Path(tmpdir.name) / "overrides-test.yaml")
        p.start()
        self.addCleanup(p.stop)
        with mock.patch.dict(os.environ, {"MQTT_HOST": "broker.local"}, clear=False):
            mqtt.configure({"mqtt": {"enabled": True, "topic_prefix": "alpaca"}}, "test")

    def test_a_set_publishes_the_new_effective_value_retained(self):
        overrides.set_override("temperature", 0.7, now=NOW)
        override.republish_effective(None, NOW)
        topics = {t: (p, r) for t, p, r in self.cap.calls}
        self.assertIn("alpaca/test/config/effective", topics)
        payload, retained = topics["alpaca/test/config/effective"]
        self.assertTrue(retained)
        self.assertIn('"temperature": 0.7', payload)
        # and the overrides ride along, as in the cycle's config event
        self.assertIn("overrides", payload)

    def test_a_broker_failure_is_swallowed(self):
        """The override itself must survive MQTT being down - the publish is
        a courtesy to the dashboard, never a dependency."""
        with mock.patch.object(mqtt, "_publisher", Capture(fail=True)):
            override.republish_effective(None, NOW)  # must not raise

    def test_disabled_mqtt_publishes_nothing(self):
        mqtt.configure({}, "test")
        override.republish_effective(None, NOW)
        self.assertEqual(self.cap.calls, [])


if __name__ == "__main__":
    unittest.main()

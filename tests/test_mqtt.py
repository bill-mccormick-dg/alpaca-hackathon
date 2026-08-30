import json
import os
import re
import unittest
from unittest import mock

from bot import mqtt


class Capture:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, topic, payload, retain):
        if self.fail:
            raise ConnectionError("broker down")
        self.calls.append((topic, payload, retain))


class MqttTest(unittest.TestCase):
    def setUp(self):
        self.cap = Capture()
        patcher = mock.patch.object(mqtt, "_publisher", self.cap)
        patcher.start()
        self.addCleanup(patcher.stop)
        mqtt._discovered.clear()
        self.addCleanup(lambda: mqtt.configure({}, "test"))

    def test_disabled_without_host_or_flag(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True}}, "test")
            self.assertFalse(mqtt.enabled())
            mqtt.configure({"mqtt": {"enabled": False, "host": "broker"}}, "test")
            self.assertFalse(mqtt.enabled())
        self.assertFalse(mqtt.publish("x", "y"))
        self.assertEqual(self.cap.calls, [])

    def test_env_host_enables_and_events_publish(self):
        with mock.patch.dict(os.environ, {"MQTT_HOST": "broker.local"}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True, "topic_prefix": "alpaca"}}, "test")
            self.assertTrue(mqtt.enabled())
            mqtt.on_event({"event": "cycle_start", "equity": 100250.5, "day_pnl": 250.5, "positions": 2})
        topics = [t for t, _, _ in self.cap.calls]
        # discovery first (retained), then the event, then retained state
        self.assertTrue(any(t.startswith("homeassistant/sensor/alpaca_test_equity/config") for t in topics))
        self.assertIn("alpaca/test/event/cycle_start", topics)
        state = {t: (p, r) for t, p, r in self.cap.calls if "/state/" in t}
        self.assertEqual(state["alpaca/test/state/equity"], ("100250.50", True))
        self.assertEqual(state["alpaca/test/state/positions"], ("2", True))
        self.assertEqual(state["alpaca/test/state/halt"], ("none", True))

    def test_discovery_payload_shape_and_only_once(self):
        with mock.patch.dict(os.environ, {"MQTT_HOST": "b"}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True}}, "official")
            mqtt.on_event({"event": "decision", "count": 0})
            mqtt.on_event({"event": "decision", "count": 2})
        disc = [(t, json.loads(p)) for t, p, r in self.cap.calls if t.startswith("homeassistant/")]
        self.assertEqual(len(disc), len(mqtt.STATE_SENSORS))  # once, not twice
        _, payload = disc[0]
        self.assertEqual(payload["device"]["identifiers"], ["alpaca_hackathon_official"])
        self.assertIn("state_topic", payload)
        # object_id is set to the id HA derives from device+entity name, so
        # both routes agree - see EntityIdDerivationTest below.
        self.assertEqual(payload["object_id"], mqtt.entity_object_id("official", "equity"))
        last = [p for t, p, r in self.cap.calls if t.endswith("/state/last_decision")]
        self.assertEqual(last, ["hold", "2 proposal(s)"])

    def test_halt_and_config_states(self):
        with mock.patch.dict(os.environ, {"MQTT_HOST": "b"}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True}}, "test")
            mqtt.on_event({"event": "daily_loss_halt", "equity": 97000})
            mqtt.on_event({"event": "config", "config_hash": "abc", "model": "m"})
        by_topic = {t: (p, r) for t, p, r in self.cap.calls}
        self.assertEqual(by_topic["alpaca-hackathon/test/state/halt"], ("daily_loss", True))
        eff = by_topic["alpaca-hackathon/test/config/effective"]
        self.assertTrue(eff[1])
        self.assertEqual(json.loads(eff[0])["config_hash"], "abc")

    def test_unlisted_events_are_not_published(self):
        with mock.patch.dict(os.environ, {"MQTT_HOST": "b"}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True}}, "test")
            mqtt.on_event({"event": "cycle_end", "actions": 0})
        self.assertEqual(self.cap.calls, [])

    def test_publisher_failure_is_swallowed(self):
        with (
            mock.patch.object(mqtt, "_publisher", Capture(fail=True)),
            mock.patch.dict(os.environ, {"MQTT_HOST": "b"}, clear=True),
        ):
            mqtt.configure({"mqtt": {"enabled": True}}, "test")
            self.assertFalse(mqtt.publish("t", {"a": 1}))
            mqtt.on_event({"event": "cycle_start", "equity": 1})  # must not raise

class EntityIdDerivationTest(unittest.TestCase):
    """Home Assistant's MQTT discovery IGNORES object_id and derives
    entity_id from slugify(device name) + "_" + slugify(entity name)
    (verified live against HA 2026 with a probe entity, both with and
    without a device block). Every dashboard reference in ansible/ assumes
    <domain>.<ENTITY_PREFIX>_<account>_<key>, so this test pins the naming
    that produces it: rename an entity carelessly and this fails rather
    than silently orphaning a dashboard card."""

    @staticmethod
    def slugify(text: str) -> str:
        return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")

    def derived(self, device_name: str, entity_name: str) -> str:
        return f"{self.slugify(device_name)}_{self.slugify(entity_name)}"

    def test_state_sensor_names_derive_the_expected_entity_ids(self):
        for account in ("official", "test"):
            device_name = mqtt.device_block(account)["name"]
            for key, attrs in mqtt.STATE_SENSORS.items():
                want = mqtt.entity_object_id(account, key)
                self.assertEqual(self.derived(device_name, attrs["name"]), want, key)

    def test_bridge_entity_names_derive_the_expected_entity_ids(self):
        import mqtt_bridge

        for topic, payload in mqtt_bridge.discovery_payloads("alpaca-hackathon"):
            device_name = payload["device"]["name"]
            derived = self.derived(device_name, payload["name"])
            self.assertEqual(derived, payload["object_id"], topic)

    def test_device_name_slugifies_to_the_prefix(self):
        for account in ("official", "test"):
            name = mqtt.device_block(account)["name"]
            self.assertEqual(self.slugify(name), f"{mqtt.ENTITY_PREFIX}_{account}")


if __name__ == "__main__":
    unittest.main()

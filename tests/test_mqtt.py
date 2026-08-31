import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import credentials, mqtt


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

        # Point every credential source at an empty temp dir. mqtt.configure()
        # resolves broker settings through the same file chain the bot uses
        # (#76), so on CT 108 - running as root, where
        # /root/.config/alpaca-hackathon/credentials-test.env really exists and
        # really carries MQTT_HOST - "disabled without a host" was true only on
        # a dev laptop. The ansible role's smoke test runs this suite ON the
        # host, which is where it surfaced.
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        tmp = Path(tmpdir.name)
        for attr, value in (
            ("PRODUCTION_CREDENTIALS_DIR", tmp),
            ("SECRETS_FILE", tmp / "secrets.yaml"),
            ("DOTENV_FILE", tmp / ".env"),
        ):
            p = mock.patch.object(credentials, attr, value)
            p.start()
            self.addCleanup(p.stop)

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
        # Every sensor announced once, not twice - both the plain state
        # sensors and the attribute-carrying report ones (#87).
        self.assertEqual(len(disc), len(mqtt.STATE_SENSORS) + len(mqtt.ATTRIBUTE_SENSORS))
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

    def test_identity_and_retry_events_reach_the_broker(self):
        """These are journaled by run_cycle/flatten; if they are not in
        EVENT_TOPICS they are silently dropped and no automation can fire on
        them - which is exactly what happened to identity_refused (#86)."""
        with mock.patch.dict(os.environ, {"MQTT_HOST": "b"}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True}}, "official")
            mqtt.on_event({"event": "identity_refused", "reason": "wrong account"})
            mqtt.on_event({"event": "identity_unverified", "reason": "no account number"})
            mqtt.on_event({"event": "decide_retry", "attempt": 1, "reason": "http timeout"})
        topics = [t for t, _, _ in self.cap.calls]

        for event in ("identity_refused", "identity_unverified", "decide_retry"):
            self.assertIn(f"alpaca-hackathon/official/event/{event}", topics)

    def test_every_journal_event_reaches_the_feed_even_unlisted_ones(self):
        """EVENT_TOPICS gates the per-event topics; the journal_feed sensor
        deliberately carries EVERYTHING (#134), so an event nobody listed is
        still visible where humans watch - and an unlisted event still gets
        no event/<name> topic of its own."""
        with mock.patch.dict(os.environ, {"MQTT_HOST": "b"}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True}}, "test")
            mqtt.on_event({"event": "brand_new_event", "detail": "x"})
        topics = [t for t, _, _ in self.cap.calls]
        self.assertIn("alpaca-hackathon/test/state/journal_feed", topics)
        self.assertNotIn("alpaca-hackathon/test/event/brand_new_event", topics)

    def test_the_last_holdout_events_are_now_listed(self):
        """predictions, cycle_end and manual_resume were journaled but
        dropped from MQTT; with them listed, event/<name> is complete."""
        for event in ("predictions", "cycle_end", "manual_resume"):
            self.assertIn(event, mqtt.EVENT_TOPICS)

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
            for key, attrs in {**mqtt.STATE_SENSORS, **mqtt.ATTRIBUTE_SENSORS}.items():
                want = mqtt.entity_object_id(account, key)
                self.assertEqual(self.derived(device_name, attrs["name"]), want, key)

    def test_bridge_entity_names_derive_the_expected_entity_ids(self):
        import mqtt_bridge

        for topic, payload in mqtt_bridge.discovery_payloads("alpaca-hackathon"):
            if not payload:
                continue  # an empty payload RETRACTS an entity; it declares nothing
            device_name = payload["device"]["name"]
            derived = self.derived(device_name, payload["name"])
            self.assertEqual(derived, payload["object_id"], topic)

    def test_device_name_slugifies_to_the_prefix(self):
        for account in ("official", "test"):
            name = mqtt.device_block(account)["name"]
            self.assertEqual(self.slugify(name), f"{mqtt.ENTITY_PREFIX}_{account}")


class CronEnvironmentTest(unittest.TestCase):
    """Regression: cron is what actually runs the bot, and it inherits
    almost no environment. bot/mqtt.py used to read MQTT_HOST straight
    from os.environ, so every scheduled cycle silently disabled the side
    channel - it traded fine and published nothing. Settings must resolve
    from the account's credentials file too."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.creds = Path(self.tmp.name)
        p = mock.patch.object(credentials, "PRODUCTION_CREDENTIALS_DIR", self.creds)
        p.start()
        self.addCleanup(p.stop)
        # Non-official accounts also resolve from secrets.yaml and .env, so those
        # have to point somewhere empty too - otherwise these assertions quietly
        # depend on whether the developer's own secrets.yaml has an mqtt: block.
        for attr, name in (("SECRETS_FILE", "secrets.yaml"), ("DOTENV_FILE", ".env")):
            patch = mock.patch.object(credentials, attr, self.creds / name)
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(lambda: mqtt.configure({}, "test"))

    def write(self, account, body):
        credentials.credentials_file(account).write_text(body)

    def test_broker_resolves_from_the_credentials_file_with_an_empty_env(self):
        self.write("official", "ALPACA_API_KEY=k\nMQTT_HOST=broker.local\nMQTT_PORT=1884\nMQTT_USERNAME=u\nMQTT_PASSWORD=p\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            s = mqtt.configure({"mqtt": {"enabled": True}}, "official")
        self.assertTrue(mqtt.enabled())
        self.assertEqual((s["host"], s["port"], s["username"]), ("broker.local", 1884, "u"))

    def test_real_env_vars_still_win_over_the_file(self):
        self.write("official", "MQTT_HOST=from-file\n")
        with mock.patch.dict(os.environ, {"MQTT_HOST": "from-env"}, clear=True):
            s = mqtt.configure({"mqtt": {"enabled": True}}, "official")
        self.assertEqual(s["host"], "from-env")

    def test_no_broker_anywhere_stays_a_silent_no_op(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            mqtt.configure({"mqtt": {"enabled": True}}, "official")
        self.assertFalse(mqtt.enabled())
        self.assertFalse(mqtt.publish("t", "x"))

    def test_each_account_reads_its_own_credentials_file(self):
        self.write("official", "MQTT_HOST=official-broker\n")
        self.write("test", "MQTT_HOST=test-broker\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mqtt.configure({"mqtt": {"enabled": True}}, "official")["host"], "official-broker")
            self.assertEqual(mqtt.configure({"mqtt": {"enabled": True}}, "test")["host"], "test-broker")


if __name__ == "__main__":
    unittest.main()

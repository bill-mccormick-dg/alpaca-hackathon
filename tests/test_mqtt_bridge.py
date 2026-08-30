import argparse
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import flatten
import mqtt_bridge
from bot import journal, mqtt, overrides
from bot import risk as risk_mod


class ApplyMessageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        logs = Path(self.tmp.name) / "logs"
        # Isolate per-account file resolution to the temp dir.
        for mod, attr in ((journal, "LOGS_DIR"), (overrides, "LOGS_DIR")):
            p = mock.patch.object(mod, attr, logs)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: journal.use_account(None))
        self.addCleanup(lambda: overrides.use_account(None))

    def test_set_applies_override_and_returns_effective_config(self):
        account, result = mqtt_bridge.apply_message({"account": "test", "key": "temperature", "value": "0.7"}, None)
        self.assertEqual(account, "test")
        self.assertTrue(result["ok"])
        self.assertEqual(result["effective"]["temperature"], 0.7)
        self.assertIn("temperature", result["effective"]["overrides"])
        self.assertEqual(result["effective"]["overrides"]["temperature"]["set_by"], "mqtt")

    def test_null_value_clears(self):
        mqtt_bridge.apply_message({"account": "test", "key": "temperature", "value": 0.7}, None)
        _, result = mqtt_bridge.apply_message({"account": "test", "key": "temperature", "value": None}, None)
        self.assertTrue(result["ok"])
        self.assertNotIn("temperature", result["effective"]["overrides"])

    def test_non_allowlisted_key_is_refused_with_reason(self):
        _, result = mqtt_bridge.apply_message({"account": "test", "key": "max_position_usd", "value": 99999}, None)
        self.assertFalse(result["ok"])
        self.assertIn("not runtime-overridable", result["error"])

    def test_bad_account_and_missing_key(self):
        _, r1 = mqtt_bridge.apply_message({"account": "Bad Name", "key": "model", "value": "x"}, None)
        self.assertFalse(r1["ok"])
        _, r2 = mqtt_bridge.apply_message({"account": "test", "value": "x"}, None)
        self.assertIn("missing 'key'", r2["error"])

    def test_default_account_is_test_never_official_by_accident(self):
        account, _ = mqtt_bridge.apply_message({"key": "temperature", "value": 0.3}, None)
        self.assertEqual(account, "test")


class ConfigPathForTest(unittest.TestCase):
    def test_explicit_override_wins_for_every_account(self):
        self.assertEqual(mqtt_bridge.config_path_for("official", "/tmp/x.yaml"), "/tmp/x.yaml")
        self.assertEqual(mqtt_bridge.config_path_for("test", "/tmp/x.yaml"), "/tmp/x.yaml")

    def test_test_account_routes_to_its_own_config_without_an_override(self):
        path = mqtt_bridge.config_path_for("test", None)
        self.assertTrue(path.endswith("config-test.yaml"))

    def test_official_falls_back_to_default_config(self):
        self.assertIsNone(mqtt_bridge.config_path_for("official", None))


class ParseHaltTopicTest(unittest.TestCase):
    def test_matches_command_halt(self):
        self.assertEqual(mqtt_bridge.parse_halt_topic("alpaca-hackathon/test/command/halt"), "test")
        self.assertEqual(mqtt_bridge.parse_halt_topic("alpaca-hackathon/official/command/halt"), "official")

    def test_ignores_other_topics(self):
        self.assertIsNone(mqtt_bridge.parse_halt_topic("alpaca-hackathon/config/set"))
        self.assertIsNone(mqtt_bridge.parse_halt_topic("alpaca-hackathon/test/config/effective"))
        self.assertIsNone(mqtt_bridge.parse_halt_topic("command/halt"))


class RunHaltTest(unittest.TestCase):
    def test_builds_the_same_args_as_flatten_py_halt_from_the_cli(self):
        captured = {}

        async def fake_run(ns):
            captured["ns"] = ns

        with mock.patch.object(flatten, "run", fake_run):
            asyncio.run(mqtt_bridge.run_halt("test", None))

        ns = captured["ns"]
        self.assertIsInstance(ns, argparse.Namespace)
        self.assertTrue(ns.halt)
        self.assertFalse(ns.expiring_only)
        self.assertEqual(ns.account, "test")
        self.assertTrue(ns.config.endswith("config-test.yaml"))

    def test_mqtt_kill_switch_never_fires_the_global_halt(self):
        """An HA button may only ever halt its own account - the global
        'halt everything' stays CLI-only, so no dashboard tap can stop the
        judged account during the scoring window."""
        captured = {}

        async def fake_run(ns):
            captured["ns"] = ns

        for account in ("test", "official"):
            with mock.patch.object(flatten, "run", fake_run):
                asyncio.run(mqtt_bridge.run_halt(account, None))
            self.assertFalse(captured["ns"].all_accounts, account)

    def test_explicit_config_override_is_respected(self):
        captured = {}

        async def fake_run(ns):
            captured["ns"] = ns

        with mock.patch.object(flatten, "run", fake_run):
            asyncio.run(mqtt_bridge.run_halt("official", "/tmp/custom.yaml"))

        self.assertEqual(captured["ns"].config, "/tmp/custom.yaml")


class DiscoveryPayloadsTest(unittest.TestCase):
    def setUp(self):
        self.payloads = dict(mqtt_bridge.discovery_payloads("alpaca-hackathon"))

    def test_kill_switch_is_a_stateful_switch_per_known_account(self):
        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            topic = f"homeassistant/switch/alpaca_{account}_kill_switch/config"
            self.assertIn(topic, self.payloads)
            payload = self.payloads[topic]
            self.assertEqual(payload["command_topic"], f"alpaca-hackathon/{account}/command/halt")
            self.assertEqual(payload["payload_on"], "HALT")
            self.assertEqual(payload["payload_off"], "RESUME")
            # State must come from the halt topic, not be assumed optimistically -
            # that is what makes the control show the real halt state.
            self.assertEqual(payload["state_topic"], f"alpaca-hackathon/{account}/state/halt")
            self.assertFalse(payload["optimistic"])
            self.assertIn("'OFF' if value == 'none' else 'ON'", payload["value_template"])

    def test_the_old_button_entity_is_retired_so_it_cannot_linger(self):
        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            self.assertIn(
                f"homeassistant/button/alpaca_{account}_kill_switch/config",
                mqtt_bridge.retired_discovery_topics(),
            )
        # And it is no longer published as a live entity.
        self.assertFalse([t for t in self.payloads if t.startswith("homeassistant/button/")])

    def test_every_overridable_knob_except_strategy_notes_is_present(self):
        from bot.overrides import OVERRIDABLE_KEYS

        expected = set(OVERRIDABLE_KEYS) - {"strategy_notes"}
        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            for key in expected:
                domain = "text" if key == "model" else "number"
                topic = f"homeassistant/{domain}/alpaca_{account}_{key}/config"
                self.assertIn(topic, self.payloads, f"missing {topic}")

    def test_every_entity_declares_the_derived_object_id(self):
        # HA ignores object_id and derives entity_id from device+entity
        # name (verified live), so object_id is set to that same derived id
        # - both routes then agree. tests/test_mqtt.py's
        # EntityIdDerivationTest checks the names actually derive it.
        for topic, payload in self.payloads.items():
            self.assertTrue(payload["object_id"].startswith(mqtt.ENTITY_PREFIX), topic)
            self.assertNotEqual(payload["object_id"], payload["unique_id"], topic)

    def test_number_knob_uses_config_set_with_a_command_template(self):
        payload = self.payloads["homeassistant/number/alpaca_test_temperature/config"]
        self.assertEqual(payload["command_topic"], "alpaca-hackathon/config/set")
        self.assertIn('"key": "temperature"', payload["command_template"])
        self.assertIn("{{ value }}", payload["command_template"])
        self.assertEqual(payload["state_topic"], "alpaca-hackathon/test/config/effective")
        self.assertEqual(payload["value_template"], "{{ value_json.temperature }}")
        self.assertEqual(payload["min"], 0)
        self.assertEqual(payload["max"], 2)

    def test_discovery_topics_are_unique(self):
        topics = [t for t, _ in mqtt_bridge.discovery_payloads("alpaca-hackathon")]
        self.assertEqual(len(topics), len(set(topics)))


class HaltFilesTestBase(unittest.TestCase):
    """Relocates bot.risk.LOGS_DIR so these tests never touch the real
    logs/ - run_resume deletes halt files, which would be live state."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        logs = Path(self.tmp.name) / "logs"
        logs.mkdir()
        for mod, attr in ((journal, "LOGS_DIR"), (overrides, "LOGS_DIR"), (risk_mod, "LOGS_DIR")):
            p = mock.patch.object(mod, attr, logs)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: journal.use_account(None))
        self.addCleanup(lambda: overrides.use_account(None))
        self.logs = logs
        self.published = []
        self.client = mock.Mock()
        self.client.publish.side_effect = lambda t, p, retain=False: self.published.append((t, p, retain))

    def states(self):
        self.published.clear()
        return mqtt_bridge.publish_halt_state(self.client, "alpaca-hackathon", None)


class HaltStateTest(HaltFilesTestBase):
    """The bug this fixes: halt state used to be published only as a side
    effect of cycle events, but a halted account runs no cycles - so the
    sensor froze at its last value. It is now derived from the halt files."""

    def test_reports_none_when_nothing_is_halted(self):
        self.assertEqual(self.states(), {"official": "none", "test": "none"})
        self.assertTrue(all(retain for _, _, retain in self.published))

    def test_tracks_a_halt_file_created_outside_the_bridge(self):
        (self.logs / "HALT_manual_test").write_text("x")
        self.assertEqual(self.states(), {"official": "none", "test": "manual"})
        # ...and notices when it is removed on the host, with no cycle needed.
        (self.logs / "HALT_manual_test").unlink()
        self.assertEqual(self.states(), {"official": "none", "test": "none"})

    def test_global_halt_shows_on_every_account(self):
        (self.logs / "HALT").write_text("x")
        self.assertEqual(self.states(), {"official": "global", "test": "global"})


class ResumeTest(HaltFilesTestBase):
    def test_resume_clears_only_this_accounts_manual_halt(self):
        (self.logs / "HALT_manual_test").write_text("x")
        (self.logs / "HALT_manual").write_text("x")
        self.assertEqual(mqtt_bridge.run_resume("test", None), "none")
        self.assertFalse((self.logs / "HALT_manual_test").exists())
        self.assertTrue((self.logs / "HALT_manual").exists())  # official untouched

    def test_resume_refuses_to_clear_a_global_halt(self):
        (self.logs / "HALT").write_text("x")
        with self.assertRaises(ValueError) as cm:
            mqtt_bridge.run_resume("test", None)
        self.assertIn("global", str(cm.exception))
        self.assertTrue((self.logs / "HALT").exists())

    def test_resume_refuses_to_clear_a_daily_loss_halt(self):
        risk = mqtt_bridge.risk_for("test", None)
        risk.daily_halt_file().write_text("x")
        with self.assertRaises(ValueError) as cm:
            mqtt_bridge.run_resume("test", None)
        self.assertIn("daily_loss", str(cm.exception))
        self.assertTrue(risk.daily_halt_file().exists())

    def test_resume_on_a_running_account_is_a_no_op(self):
        self.assertEqual(mqtt_bridge.run_resume("test", None), "none")


class PublishEffectiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        logs = Path(self.tmp.name) / "logs"
        for mod, attr in ((journal, "LOGS_DIR"), (overrides, "LOGS_DIR")):
            p = mock.patch.object(mod, attr, logs)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: journal.use_account(None))
        self.addCleanup(lambda: overrides.use_account(None))

    def test_publishes_retained_effective_config_for_every_known_account(self):
        published = []
        client = mock.Mock()
        client.publish.side_effect = lambda topic, payload, retain=False: published.append((topic, retain))

        mqtt_bridge.publish_effective(client, "alpaca-hackathon", None)

        topics = {t for t, _ in published}
        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            self.assertIn(f"alpaca-hackathon/{account}/config/effective", topics)
        self.assertTrue(all(retain for _, retain in published))


if __name__ == "__main__":
    unittest.main()

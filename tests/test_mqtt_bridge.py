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
                # Derived from NUMBER_KNOBS rather than naming the selects,
                # so adding a third select needs no change here. This used to
                # read `"select" if key == "model" else "number"`, which broke
                # the moment review_model arrived.
                if key in mqtt_bridge.NUMBER_KNOBS:
                    domain = "number"
                elif key in mqtt_bridge.BOOL_KNOBS:
                    domain = "switch"
                else:
                    domain = "select"
                topic = f"homeassistant/{domain}/alpaca_{account}_{key}/config"
                self.assertIn(topic, self.payloads, f"missing {topic}")
                # A non-empty payload, specifically. This assertion used to
                # look for the model knob under `text`, where the only thing
                # present was the RETRACTION of the old text entity - an empty
                # payload that deletes it. The test therefore passed while the
                # model knob did not exist in Home Assistant at all.
                self.assertTrue(
                    self.payloads[topic],
                    f"{topic} is an empty payload - that retracts the entity, it does not declare it",
                )

    def test_every_entity_declares_the_derived_object_id(self):
        # HA ignores object_id and derives entity_id from device+entity
        # name (verified live), so object_id is set to that same derived id
        # - both routes then agree. tests/test_mqtt.py's
        # EntityIdDerivationTest checks the names actually derive it.
        for topic, payload in self.payloads.items():
            if not payload:
                continue  # retraction, not a declaration
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

    def _all(self, state):
        """Expected mapping with every known account at `state`.

        Derived from KNOWN_ACCOUNTS rather than spelled out: these once
        hardcoded {"official", "test"} and broke the moment a third variant
        was added, which said nothing about the behaviour under test."""
        return dict.fromkeys(mqtt_bridge.KNOWN_ACCOUNTS, state)

    def test_reports_none_when_nothing_is_halted(self):
        self.assertEqual(self.states(), self._all("none"))
        self.assertTrue(all(retain for _, _, retain in self.published))

    def test_tracks_a_halt_file_created_outside_the_bridge(self):
        (self.logs / "HALT_manual_test").write_text("x")

        self.assertEqual(self.states(), {**self._all("none"), "test": "manual"})

        # ...and notices when it is removed on the host, with no cycle needed.
        (self.logs / "HALT_manual_test").unlink()
        self.assertEqual(self.states(), self._all("none"))

    def test_a_per_account_halt_does_not_leak_to_the_others(self):
        """The whole point of per-account halt files: a challenger breaching
        its cutoff must never stop the judged account."""
        (self.logs / "HALT_manual_test").write_text("x")

        states = self.states()

        self.assertEqual(states["test"], "manual")
        self.assertTrue(all(v == "none" for k, v in states.items() if k != "test"))

    def test_global_halt_shows_on_every_account(self):
        (self.logs / "HALT").write_text("x")
        self.assertEqual(self.states(), self._all("global"))


class TransientStateTest(HaltFilesTestBase):
    """A HALT runs flatten end to end and can take the whole verify
    timeout. The tile must respond to the press immediately, and the
    heartbeat must not stomp that in-progress state with the file-derived
    one - the halt file does not exist yet while flatten is still running."""

    def tearDown(self):
        with mqtt_bridge._inflight_lock:
            mqtt_bridge._inflight.clear()

    def published_halt(self):
        return [(t, p) for t, p, _ in self.published if t.endswith("/state/halt")]

    def test_begin_command_marks_in_flight_and_refuses_a_second(self):
        self.assertTrue(mqtt_bridge.begin_command("test"))
        self.assertFalse(mqtt_bridge.begin_command("test"))  # no two flattens at once
        self.assertTrue(mqtt_bridge.begin_command("official"))  # other accounts unaffected

    def test_run_command_resolves_the_state_and_clears_in_flight(self):
        mqtt_bridge.begin_command("test")
        self.published.clear()
        with mock.patch.object(mqtt_bridge, "run_resume", lambda *a: "none"):
            mqtt_bridge.run_command(self.client, "alpaca-hackathon", "test", "RESUME", None)
        self.assertIn(("alpaca-hackathon/test/state/halt", "none"), self.published_halt())
        with mqtt_bridge._inflight_lock:
            self.assertNotIn("test", mqtt_bridge._inflight)

    def test_run_command_reports_failure_and_still_resolves(self):
        mqtt_bridge.begin_command("test")
        self.published.clear()

        def boom(*a):
            raise ValueError("nope")

        with mock.patch.object(mqtt_bridge, "run_resume", boom):
            mqtt_bridge.run_command(self.client, "alpaca-hackathon", "test", "RESUME", None)
        self.assertTrue([t for t, _, _ in self.published if t.endswith("/command/error")])
        self.assertIn(("alpaca-hackathon/test/state/halt", "none"), self.published_halt())
        with mqtt_bridge._inflight_lock:
            self.assertNotIn("test", mqtt_bridge._inflight)

    def test_heartbeat_does_not_overwrite_an_in_flight_account(self):
        mqtt_bridge.begin_command("test")
        self.published.clear()
        # Heartbeat runs mid-flatten: the halt file is not written yet, so a
        # naive resync would publish "none" and flick the tile back to green.
        states = mqtt_bridge.publish_halt_state(self.client, "alpaca-hackathon", None)

        # The invariant: the in-flight account is skipped, every other known
        # account is still resynced. Listing the others by name made this break
        # when a third variant was added, which told us nothing.
        self.assertNotIn("test", states)
        others = [a for a in mqtt_bridge.KNOWN_ACCOUNTS if a != "test"]
        self.assertEqual(
            self.published_halt(),
            [(f"alpaca-hackathon/{a}/state/halt", "none") for a in others],
        )

    def test_force_resolves_the_transient_state_to_the_truth(self):
        mqtt_bridge.begin_command("test")
        (self.logs / "HALT_manual_test").write_text("x")
        self.published.clear()
        states = mqtt_bridge.publish_halt_state(self.client, "alpaca-hackathon", None, force=True)
        self.assertEqual(states["test"], "manual")
        self.assertIn(("alpaca-hackathon/test/state/halt", "manual"), self.published_halt())
        # And the account is no longer in flight, so the heartbeat resumes.
        self.published.clear()
        self.assertEqual(mqtt_bridge.publish_halt_state(self.client, "alpaca-hackathon", None)["test"], "manual")

    def test_a_failed_command_still_resolves_rather_than_sticking_amber(self):
        mqtt_bridge.begin_command("test")
        self.published.clear()
        # No halt file was ever created (the command failed): force resync
        # must still clear the in-flight mark and report the truth.
        states = mqtt_bridge.publish_halt_state(self.client, "alpaca-hackathon", None, force=True)
        self.assertEqual(states["test"], "none")
        with mqtt_bridge._inflight_lock:
            self.assertNotIn("test", mqtt_bridge._inflight)


class SourceWatchTest(unittest.TestCase):
    """A deploy rsyncs new code but cannot restart the bridge (the runner is
    unprivileged and CT 108 has no sudo), so the bridge watches its own
    source and exits to let systemd restart it on the new code."""

    def tearDown(self):
        with mqtt_bridge._inflight_lock:
            mqtt_bridge._inflight.clear()

    def test_fingerprint_covers_the_bridge_and_the_bot_package(self):
        names = [p for p, _, _ in mqtt_bridge.source_fingerprint()]
        self.assertTrue(any(n.endswith("mqtt_bridge.py") for n in names))
        self.assertTrue(any(n.endswith("flatten.py") for n in names))
        self.assertTrue(any(n.endswith("bot/risk.py") for n in names))
        self.assertTrue(any(n.endswith("bot/mqtt.py") for n in names))

    def test_exits_when_the_source_changes(self):
        readings = iter([("a",), ("a",), ("b",)])
        with (
            mock.patch.object(mqtt_bridge.time, "sleep", lambda _s: None),
            mock.patch.object(mqtt_bridge.os, "_exit", side_effect=SystemExit) as ex,
            self.assertRaises(SystemExit),
        ):
            mqtt_bridge.watch_source_and_exit(0, _fingerprint=lambda: next(readings))
        ex.assert_called_once_with(0)

    def test_never_exits_while_a_command_is_in_flight(self):
        mqtt_bridge.begin_command("test")
        readings = iter([("a",), ("b",), ("b",), ("b",)])

        def fingerprint():
            try:
                return next(readings)
            except StopIteration:
                raise KeyboardInterrupt from None  # end the loop for the test

        with (
            mock.patch.object(mqtt_bridge.time, "sleep", lambda _s: None),
            mock.patch.object(mqtt_bridge.os, "_exit", side_effect=SystemExit) as ex,
            self.assertRaises(KeyboardInterrupt),
        ):
            mqtt_bridge.watch_source_and_exit(0, _fingerprint=fingerprint)
        ex.assert_not_called()  # a flatten must finish and publish first


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


class AccountConfigMappingTest(unittest.TestCase):
    """Each account's knobs must be primed from the config it actually runs.

    Caught from a dashboard screenshot: all three accounts showed
    moonshotai/Kimi-K2-Instruct, while the `test` account has been running
    Qwen the whole time. A knob primed from the wrong file reports a model,
    a stop-loss or a strike band the account is not using - and the A/B
    dashboard is the one place someone goes to check exactly that."""

    def test_every_variant_account_maps_to_its_own_config(self):
        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            if account == "official":
                continue  # official is config.yaml, the fallback
            self.assertIn(
                account, mqtt_bridge.ACCOUNT_CONFIG_PATH,
                f"{account} would be primed from config.yaml, not its own config",
            )

    def test_mapped_configs_exist_on_disk(self):
        for account, path in mqtt_bridge.ACCOUNT_CONFIG_PATH.items():
            self.assertTrue(Path(path).is_file(), f"{account} maps to a missing config: {path}")

    def test_mapped_configs_are_distinct(self):
        paths = list(mqtt_bridge.ACCOUNT_CONFIG_PATH.values())
        self.assertEqual(len(paths), len(set(paths)), "two accounts share a config file")


MODEL_CONFIG = {"model_prices": {"a/one": {}, "b/two": {}}}


class ModelIsADropdownTest(unittest.TestCase):
    """`model` is the only knob bot/overrides.py accepts as any non-empty
    string - every other one is range-checked. A thumb-typo on a phone was
    therefore writable, and the next cycle would fail at the model call, burn
    its retries and forfeit the slot. A select makes the wrong value
    unreachable from the dashboard; the CLI keeps the escape hatch."""

    def payloads(self, config=None):
        return dict(mqtt_bridge.discovery_payloads("alpaca-hackathon", config))

    def test_model_is_published_as_a_select_with_options(self):
        p = self.payloads(MODEL_CONFIG)
        topic = "homeassistant/select/alpaca_official_model/config"

        self.assertIn(topic, p)
        self.assertEqual(p[topic]["options"], ["a/one", "b/two"])

    def test_options_come_from_model_prices(self):
        """One list, not two: a model costed in config.yaml is offered here."""
        self.assertEqual(mqtt_bridge.model_options(MODEL_CONFIG), ["a/one", "b/two"])

    def test_falls_back_to_the_default_model_when_none_are_listed(self):
        self.assertEqual(mqtt_bridge.model_options({}), [mqtt_bridge.DEFAULT_MODEL])

    def test_the_old_text_entity_is_retracted_before_the_select_is_published(self):
        """The select and the text entity it replaced share a unique_id, and
        Home Assistant will not re-create a unique_id it already holds in a
        different domain. Publishing the select first means HA ignores it and
        the later retraction then removes the only model entity that existed -
        which is what shipped: every controls card read "Entity not found".

        So the retraction has to be in retired_discovery_topics(), which
        publish_discovery() sends before anything else."""
        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            self.assertIn(
                f"homeassistant/text/alpaca_{account}_model/config",
                mqtt_bridge.retired_discovery_topics(),
            )

        published = []

        class Recorder:
            def publish(self, topic, payload, retain=False):
                published.append(topic)

        mqtt_bridge.publish_discovery(Recorder(), "alpaca-hackathon", MODEL_CONFIG)
        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            retract = published.index(f"homeassistant/text/alpaca_{account}_model/config")
            select = published.index(f"homeassistant/select/alpaca_{account}_model/config")
            self.assertLess(retract, select, f"{account}: select published before the text retraction")

    def test_every_account_gets_the_dropdown(self):
        p = self.payloads(MODEL_CONFIG)

        for account in mqtt_bridge.KNOWN_ACCOUNTS:
            self.assertIn(f"homeassistant/select/alpaca_{account}_model/config", p)


if __name__ == "__main__":
    unittest.main()

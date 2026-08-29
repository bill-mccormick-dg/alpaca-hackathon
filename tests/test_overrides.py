import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from bot import overrides
from bot.risk import EASTERN

NOW = datetime(2026, 9, 1, 10, 30, tzinfo=EASTERN)  # Tue, mid-session


class ValidateTest(unittest.TestCase):
    def test_coerces_strings_from_cli_or_mqtt(self):
        self.assertEqual(overrides.validate("temperature", "0.5"), 0.5)
        self.assertEqual(overrides.validate("max_tokens", "600"), 600)
        self.assertEqual(overrides.validate("model", " Qwen/Qwen3-8B "), "Qwen/Qwen3-8B")

    def test_rejects_unknown_key_with_allowlist_in_message(self):
        with self.assertRaises(ValueError) as ctx:
            overrides.validate("max_position_usd", 99999)
        self.assertIn("not runtime-overridable", str(ctx.exception))
        self.assertIn("strategy_notes", str(ctx.exception))

    def test_rejects_out_of_range_and_garbage(self):
        with self.assertRaises(ValueError):
            overrides.validate("temperature", 5)
        with self.assertRaises(ValueError):
            overrides.validate("stop_loss_pct", "lots")
        with self.assertRaises(ValueError):
            overrides.validate("strategy_notes", "   ")


class DefaultUntilTest(unittest.TestCase):
    def test_mid_session_expires_at_four_pm_eastern_today(self):
        self.assertEqual(overrides.default_until(NOW), datetime(2026, 9, 1, 16, 0, tzinfo=EASTERN))

    def test_evening_tweak_lasts_through_tomorrows_close(self):
        evening = datetime(2026, 9, 1, 19, 0, tzinfo=EASTERN)
        self.assertEqual(overrides.default_until(evening), datetime(2026, 9, 2, 16, 0, tzinfo=EASTERN))


class FileRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "logs" / "overrides.yaml"

    def test_set_then_active(self):
        entry = overrides.set_override("temperature", "0.7", now=NOW, path=self.path)
        self.assertEqual(entry["value"], 0.7)
        self.assertEqual(entry["until"], "2026-09-01T16:00-04:00")
        self.assertEqual(entry["set_by"], "cli")

        active = overrides.active_overrides(NOW, path=self.path)
        self.assertEqual(active["temperature"]["value"], 0.7)

    def test_expired_override_is_ignored_and_pruned_on_next_write(self):
        overrides.set_override("temperature", 0.7, now=NOW, path=self.path)
        later = NOW + timedelta(hours=6)  # 16:30 ET, past the default expiry
        self.assertEqual(overrides.active_overrides(later, path=self.path), {})

        overrides.set_override("max_tokens", 500, now=later, path=self.path)
        on_disk = yaml.safe_load(self.path.read_text())
        self.assertNotIn("temperature", on_disk)
        self.assertIn("max_tokens", on_disk)

    def test_explicit_until_is_honoured(self):
        until = datetime(2026, 9, 3, 16, 0, tzinfo=EASTERN)
        overrides.set_override("model", "Qwen/Qwen3-8B", until=until, now=NOW, path=self.path)
        self.assertIn("model", overrides.active_overrides(NOW + timedelta(days=1), path=self.path))
        self.assertEqual(overrides.active_overrides(until + timedelta(minutes=1), path=self.path), {})

    def test_set_by_is_recorded(self):
        entry = overrides.set_override("stop_loss_pct", 35, set_by="mqtt", now=NOW, path=self.path)
        self.assertEqual(entry["set_by"], "mqtt")

    def test_unknown_or_bad_entries_on_disk_are_ignored_not_fatal(self):
        self.path.parent.mkdir()
        self.path.write_text(
            yaml.safe_dump(
                {
                    "max_position_usd": {"value": 1, "until": None},  # not allowlisted
                    "temperature": {"value": "hot", "until": None},  # bad value
                    "max_tokens": {"value": 700, "until": None},  # fine, never expires
                    "junk": "not a dict",
                }
            )
        )
        active = overrides.active_overrides(NOW, path=self.path)
        self.assertEqual(list(active), ["max_tokens"])

    def test_corrupt_yaml_reads_as_empty(self):
        self.path.parent.mkdir()
        self.path.write_text("{ this is: [not yaml")
        self.assertEqual(overrides.active_overrides(NOW, path=self.path), {})

    def test_clear_one_and_all(self):
        overrides.set_override("temperature", 0.7, now=NOW, path=self.path)
        overrides.set_override("max_tokens", 500, now=NOW, path=self.path)
        self.assertTrue(overrides.clear_override("temperature", path=self.path))
        self.assertFalse(overrides.clear_override("temperature", path=self.path))
        self.assertEqual(list(overrides.active_overrides(NOW, path=self.path)), ["max_tokens"])
        self.assertEqual(overrides.clear_all(path=self.path), 1)
        self.assertEqual(overrides.active_overrides(NOW, path=self.path), {})

    def test_write_is_atomic_no_temp_files_left(self):
        overrides.set_override("temperature", 0.7, now=NOW, path=self.path)
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name != "overrides.yaml"]
        self.assertEqual(leftovers, [])


class PerAccountOverridesTest(unittest.TestCase):
    def test_file_per_account(self):
        self.assertEqual(overrides.overrides_file("official").name, "overrides.yaml")
        self.assertEqual(overrides.overrides_file(None).name, "overrides.yaml")
        self.assertEqual(overrides.overrides_file("qwen-a").name, "overrides-qwen-a.yaml")

    def test_use_account_repoints_default_used_by_readers_and_writers(self):
        original = overrides.OVERRIDES_FILE
        try:
            with tempfile.TemporaryDirectory() as d:
                overrides.OVERRIDES_FILE = Path(d) / "overrides-qwen-a.yaml"
                overrides.set_override("temperature", 0.9, now=NOW)  # no explicit path
                self.assertTrue((Path(d) / "overrides-qwen-a.yaml").exists())
                self.assertEqual(overrides.active_overrides(NOW)["temperature"]["value"], 0.9)
        finally:
            overrides.OVERRIDES_FILE = original


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from bot import overrides
from bot.config import CONFIG_FILE, config_provenance, load_config
from bot.risk import EASTERN

NOW = datetime(2026, 9, 1, 10, 30, tzinfo=EASTERN)


class OverrideLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ov = Path(self.tmp.name) / "overrides.yaml"

    def test_no_overrides_file_is_pure_yaml_plus_empty_provenance(self):
        config = load_config(now=NOW, overrides_path=self.ov)
        self.assertEqual(config["_overrides"], {})
        self.assertEqual(config["model"], "moonshotai/Kimi-K2-Instruct")

    def test_active_override_beats_yaml(self):
        overrides.set_override("temperature", 0.9, now=NOW, path=self.ov)
        config = load_config(now=NOW, overrides_path=self.ov)
        self.assertEqual(config["temperature"], 0.9)
        self.assertEqual(config["_overrides"]["temperature"]["set_by"], "cli")

    def test_expired_override_does_not(self):
        overrides.set_override("temperature", 0.9, now=NOW, path=self.ov)
        config = load_config(now=NOW + timedelta(hours=8), overrides_path=self.ov)
        self.assertEqual(config["temperature"], 0.2)  # config.yaml's value
        self.assertEqual(config["_overrides"], {})

    def test_non_allowlisted_key_on_disk_never_reaches_config(self):
        self.ov.write_text("max_position_usd:\n  value: 999999\n  until: null\n")
        config = load_config(now=NOW, overrides_path=self.ov)
        self.assertEqual(config["max_position_usd"], 5000)

    def test_provenance_hashes_notes_and_lists_overrides(self):
        overrides.set_override("model", "Qwen/Qwen3-8B", now=NOW, path=self.ov)
        prov = config_provenance(load_config(now=NOW, overrides_path=self.ov))
        self.assertEqual(prov["model"], "Qwen/Qwen3-8B")
        self.assertIn("model", prov["overrides"])
        self.assertEqual(len(prov["config_hash"]), 12)
        self.assertTrue(prov["strategy_notes_head"].startswith("THESIS"))
        self.assertNotIn("strategy_notes", prov)  # hashed, not dumped

    def test_provenance_hash_changes_with_an_override(self):
        base = config_provenance(load_config(now=NOW, overrides_path=self.ov))["config_hash"]
        overrides.set_override("stop_loss_pct", 25, now=NOW, path=self.ov)
        changed = config_provenance(load_config(now=NOW, overrides_path=self.ov))["config_hash"]
        self.assertNotEqual(base, changed)


class LoadConfigTest(unittest.TestCase):
    def test_config_file_exists(self):
        self.assertTrue(CONFIG_FILE.exists())

    def test_loads_the_real_config_file(self):
        config = load_config()
        self.assertIsInstance(config["underlyings"], list)
        self.assertGreater(len(config["underlyings"]), 0)
        self.assertIn("max_position_usd", config)
        self.assertIn("max_positions", config)
        self.assertIn("max_contracts_per_order", config)
        self.assertIn("daily_loss_cutoff_pct", config)
        self.assertIn("min_days_to_expiration", config)
        self.assertIn("max_days_to_expiration", config)
        self.assertLess(config["min_days_to_expiration"], config["max_days_to_expiration"])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from bot import overrides
from bot.config import CONFIG_FILE, config_provenance, load_config, resolve_review_model
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


class ResolveReviewModelTest(unittest.TestCase):
    """A model grading its own reasoning is the weakest form of review, so the
    default is the first preference entry that did not trade the day."""

    PREF = ("b-model", "a-model", "c-model")

    def test_picks_the_first_preference_that_is_not_the_trading_model(self):
        cfg = {"model": "b-model", "review_model_preference": list(self.PREF)}

        self.assertEqual(resolve_review_model(cfg), "a-model")

    def test_explicit_review_model_wins(self):
        cfg = {"model": "a-model", "review_model": "c-model",
               "review_model_preference": list(self.PREF)}

        self.assertEqual(resolve_review_model(cfg), "c-model")

    def test_none_when_every_preference_is_the_trading_model(self):
        cfg = {"model": "a-model", "review_model_preference": ["a-model"]}

        self.assertIsNone(resolve_review_model(cfg))

    def test_none_without_a_preference_list(self):
        self.assertIsNone(resolve_review_model({"model": "a-model"}))

    def test_recomputed_so_switching_the_trading_model_cannot_collide(self):
        """The trading model is changeable at runtime from the dashboard. Were
        the choice resolved once and stored, switching the account onto the
        reviewer would silently end the independence this key exists for."""
        cfg = {"model": "a-model", "review_model_preference": list(self.PREF)}
        self.assertEqual(resolve_review_model(cfg), "b-model")

        cfg["model"] = "b-model"

        self.assertEqual(resolve_review_model(cfg), "a-model")

    def test_provenance_publishes_the_resolved_value(self):
        """config/effective feeds the dashboard selector's state, so it must
        carry the model that will actually run, not an unset raw key."""
        cfg = {"model": "b-model", "review_model_preference": list(self.PREF)}

        self.assertEqual(config_provenance(cfg)["review_model"], "a-model")

    def test_review_model_does_not_churn_the_config_hash(self):
        """It cannot affect trading, so it must not invalidate the hash that
        attributes a P&L change to a config change."""
        base = {"model": "b-model", "review_model_preference": list(self.PREF)}
        pinned = {**base, "review_model": "c-model"}

        self.assertEqual(config_provenance(base)["config_hash"],
                         config_provenance(pinned)["config_hash"])


if __name__ == "__main__":
    unittest.main()

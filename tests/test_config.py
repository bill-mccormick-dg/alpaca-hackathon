import unittest

from bot.config import CONFIG_FILE, load_config


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

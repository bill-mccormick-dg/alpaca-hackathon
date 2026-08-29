import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mqtt_bridge
from bot import journal, overrides


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


if __name__ == "__main__":
    unittest.main()

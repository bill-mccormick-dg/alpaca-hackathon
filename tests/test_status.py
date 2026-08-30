import tempfile
import unittest
from pathlib import Path

import status
from bot.risk import RiskManager


def _config():
    return {
        "underlyings": ["AAPL"], "max_position_usd": 1, "max_positions": 1, "max_contracts_per_order": 1,
        "daily_loss_cutoff_pct": 1, "min_days_to_expiration": 1, "max_days_to_expiration": 2,
        "trade_start": "09:45", "trade_end": "15:45", "last_entry": "15:15",
    }


class HaltStateTest(unittest.TestCase):
    def test_reports_global_manual_and_daily_halt_files(self):
        with tempfile.TemporaryDirectory() as d:
            risk = RiskManager(_config(), logs_dir=Path(d))
            self.assertEqual(
                status.halt_state(risk),
                {"global_halt": False, "manual_halt": False, "daily_halt": False},
            )
            risk.manual_halt_file().write_text("x")
            self.assertTrue(status.halt_state(risk)["manual_halt"])
            risk.daily_halt_file().write_text("x")
            self.assertTrue(status.halt_state(risk)["daily_halt"])
            risk.global_halt_file().write_text("x")
            self.assertTrue(status.halt_state(risk)["global_halt"])


class FormatPositionsTest(unittest.TestCase):
    def test_trims_and_converts_numeric_strings(self):
        raw = [
            {
                "symbol": "AAPL260204C00200000", "asset_class": "us_option", "qty": "2",
                "avg_entry_price": "5.10", "market_value": "1100.00", "unrealized_pl": "80.00", "noise": 1,
            }
        ]
        out = status.format_positions(raw)
        self.assertEqual(out[0]["symbol"], "AAPL260204C00200000")
        self.assertEqual(out[0]["qty"], 2.0)
        self.assertEqual(out[0]["unrealized_pl"], 80.0)
        self.assertNotIn("noise", out[0])

    def test_missing_or_garbage_numbers_become_none_not_a_crash(self):
        out = status.format_positions([{"symbol": "SPY", "qty": "n/a"}])
        self.assertIsNone(out[0]["qty"])
        self.assertIsNone(out[0]["market_value"])

    def test_skips_non_dict_entries(self):
        self.assertEqual(status.format_positions(["junk", None]), [])


if __name__ == "__main__":
    unittest.main()

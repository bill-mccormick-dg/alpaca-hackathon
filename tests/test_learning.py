import unittest
from datetime import datetime, timedelta

from bot import learning
from bot.risk import EASTERN

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=EASTERN)


def trip(symbol, pnl, reason, minutes=60, dte=3, instrument="call", pct=None):
    return {"symbol": symbol, "qty": 2, "entry_price": 2.0, "entry_time": T0, "exit_time": T0 + timedelta(minutes=minutes),
            "hold_minutes": float(minutes), "pnl": pnl, "pnl_pct": pct if pct is not None else pnl / 4,
            "exit_reason": reason, "dte_at_entry": dte, "instrument": instrument, "underlying": symbol[:3]}


TRIPS = [trip("SPY260904C00770000", 240.0, "take_profit"), trip("QQQ260911P00700000", -160.0, "stop_loss", dte=10, instrument="put")]
POSITIONS = [{"symbol": "NVDA260904C00220000", "qty": 3.0, "avg_entry_price": 2.5, "current_price": 2.0}]
RECORDS = [
    {"event": "order_rejected", "symbol": "TSLA", "detail": "TSLA not in underlyings whitelist"},
    {"event": "order_rejected", "symbol": "TSLA", "detail": "TSLA not in underlyings whitelist"},
    {"event": "order_rejected", "symbol": "SPY260904C00780000", "detail": "position value 6100.00 exceeds max_position_usd 5000"},
    {"event": "decision", "raw": "[]"},
]


class LearningBlockTest(unittest.TestCase):
    def test_empty_inputs_give_empty_block(self):
        self.assertEqual(learning.build_learning_block([], [], []), "")

    def test_block_has_aggregate_trips_positions_and_rejections(self):
        block = learning.build_learning_block(TRIPS, POSITIONS, RECORDS)
        self.assertIn("RECENT OUTCOMES", block)
        self.assertIn("2 closed trades in the window: net +80, 1 winners", block)
        self.assertIn("SPY260904C00770000 x2", block)
        self.assertIn("exit: take_profit", block)
        self.assertIn("holding NVDA260904C00220000 x3 @ 2.5, -20.0% vs entry", block)
        self.assertIn("3 proposals REJECTED", block)
        self.assertIn("'not in underlyings whitelist': 2", block)
        self.assertIn("'exceeds max_position_usd': 1", block)
        self.assertIn("do not re-propose the same idea", block)

    def test_recent_trips_limited_and_newest_first(self):
        many = [trip(f"SPY26090{i % 9 + 1}C00770000", 10.0, "model", minutes=i) for i in range(30)]
        lines = learning.recent_trips_lines(many, 5)
        self.assertEqual(len(lines), 5)
        self.assertIn("held 29m", lines[0])

    def test_block_is_capped(self):
        many = [trip(f"SPY26090{i % 9 + 1}C0077{i % 10}000", 10.0, "model", minutes=i) for i in range(200)]
        block = learning.build_learning_block(many, [], [], max_trades=200)
        self.assertLessEqual(len(block), learning.MAX_CHARS)
        self.assertTrue(block.rstrip().endswith("(...)"))

    def test_no_rejections_means_no_rejection_line(self):
        block = learning.build_learning_block(TRIPS, [], [{"event": "decision"}])
        self.assertNotIn("REJECTED", block)


if __name__ == "__main__":
    unittest.main()

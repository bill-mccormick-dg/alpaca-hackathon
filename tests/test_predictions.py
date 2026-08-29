import unittest
from datetime import datetime, timezone

from bot import predictions

NOW = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)  # Mon 10:00 ET


def market(event, lo, hi, bid, ask, close="2026-08-31T20:00:00Z", volume=100, last=None):
    return {
        "event_ticker": event, "ticker": f"{event}-{lo or 'T'}{hi or ''}", "status": "open", "close_time": close,
        "floor_strike": lo, "cap_strike": hi, "yes_bid_dollars": bid, "yes_ask_dollars": ask,
        "last_price_dollars": last, "volume_fp": volume,
    }


TODAY = [
    market("E-TODAY", None, 7400, 0.04, 0.06),
    market("E-TODAY", 7400, 7424.99, 0.10, 0.12),
    market("E-TODAY", 7425, 7449.99, 0.28, 0.32, volume=500),
    market("E-TODAY", 7450, 7474.99, 0.30, 0.34, volume=600),
    market("E-TODAY", 7475, 7499.99, 0.12, 0.14),
    market("E-TODAY", 7500, None, 0.05, 0.07),
]
FRIDAY = [market("E-FRI", 7400, 7424.99, 0.5, 0.5, close="2026-09-04T20:00:00Z")]


class NearestEventTest(unittest.TestCase):
    def test_picks_earliest_open_event_still_ahead(self):
        past = [market("E-PAST", 7400, 7424.99, 0.5, 0.5, close="2026-08-28T20:00:00Z")]
        ev = predictions.nearest_event(past + FRIDAY + TODAY, NOW)
        self.assertEqual({m["event_ticker"] for m in ev}, {"E-TODAY"})

    def test_nothing_ahead(self):
        self.assertEqual(predictions.nearest_event([market("E", 1, 2, 0.5, 0.5, close="2026-01-01T00:00:00Z")], NOW), [])


class SummarizeTest(unittest.TestCase):
    def test_distribution_summary_with_reference(self):
        s = predictions.summarize_range_event(TODAY, reference=7440.0)
        self.assertEqual(s["buckets"], 6)
        self.assertAlmostEqual(s["implied_median"], 7462.495, delta=0.02)  # cumulative crosses 0.5 in the 7450-7475 bucket
        self.assertGreater(s["p_above_reference"], 0.5)
        self.assertLess(s["p_down_over_1pct"], 0.2)
        self.assertEqual(s["volume"], 1500.0)  # 4 x 100 + 500 + 600
        self.assertEqual(s["top_buckets"][0]["range"], "7450.0-7474.99")
        self.assertAlmostEqual(sum(b["p"] for b in s["top_buckets"]), 0.87, delta=0.05)
        self.assertAlmostEqual(s["implied_move_pct"], (7462.495 / 7440 - 1) * 100, delta=0.01)

    def test_without_reference_still_gives_median(self):
        s = predictions.summarize_range_event(TODAY, reference=None)
        self.assertNotIn("p_above_reference", s)
        self.assertAlmostEqual(s["implied_median"], 7462.495, delta=0.02)

    def test_no_quotes_yet_returns_none(self):
        unquoted = [market("E", 7400, 7424.99, None, None)]
        self.assertIsNone(predictions.summarize_range_event(unquoted, 7400))

    def test_last_price_used_when_no_bid_ask(self):
        s = predictions.summarize_range_event([market("E", 7400, 7424.99, None, None, last=0.6),
                                                market("E", 7425, 7449.99, None, None, last=0.4)], None)
        self.assertAlmostEqual(s["implied_median"], 7412.495, delta=0.01)


class PromptBlockTest(unittest.TestCase):
    def test_empty_when_nothing(self):
        self.assertEqual(predictions.prompt_block({}), "")

    def test_renders_facts(self):
        s = predictions.summarize_range_event(TODAY, reference=7440.0)
        s["series"] = "KXINX"
        block = predictions.prompt_block({"SPY": s})
        self.assertIn("PREDICTION MARKETS", block)
        self.assertIn("SPY via KXINX", block)
        self.assertIn("prior close 7,440", block)
        self.assertIn("P(above prior close)", block)
        self.assertIn("a PRIOR to weigh, not a signal to copy", block)


if __name__ == "__main__":
    unittest.main()

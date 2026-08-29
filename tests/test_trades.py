import unittest
from datetime import datetime, timedelta

from bot import trades
from bot.risk import EASTERN

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=EASTERN)
CALL = "SPY260904C00770000"  # 3 DTE from Sep 1
PUT = "QQQ260911P00700000"  # 10 DTE


def fill(symbol, side, qty, price, minutes, reason=None):
    return {"symbol": symbol, "side": side, "qty": qty, "price": price,
            "filled_at": T0 + timedelta(minutes=minutes), "reason": reason}


JOURNALED = {
    "o-stop": "stop_loss (-42.0% vs entry)",
    "o-tp": "take_profit (+61.3% vs entry)",
    "o-exp": "expiry",
    "o-model": "thesis changed: momentum faded",
    "hb-20260901-101500-SPY-sell": "model sell via client id",
}


class ClassifyExitTest(unittest.TestCase):
    JOURNALED = JOURNALED

    def test_deterministic_exit_reasons(self):
        self.assertEqual(trades.classify_exit("o-stop", None, self.JOURNALED), trades.STOP_LOSS)
        self.assertEqual(trades.classify_exit("o-tp", None, self.JOURNALED), trades.TAKE_PROFIT)
        self.assertEqual(trades.classify_exit("o-exp", None, self.JOURNALED), trades.EXPIRY)

    def test_other_journaled_sell_is_the_model(self):
        self.assertEqual(trades.classify_exit("o-model", None, self.JOURNALED), trades.MODEL)

    def test_client_order_id_fallback(self):
        self.assertEqual(trades.classify_exit("unknown", "hb-20260901-101500-SPY-sell", self.JOURNALED), trades.MODEL)

    def test_unjournaled_sell_is_flatten(self):
        self.assertEqual(trades.classify_exit("nope", "nope", self.JOURNALED), trades.FLATTEN)
        self.assertEqual(trades.classify_exit(None, None, self.JOURNALED), trades.FLATTEN)


class PairRoundTripsTest(unittest.TestCase):
    def test_option_pnl_uses_100_multiplier_and_dte_at_entry(self):
        trips, still_open = trades.pair_round_trips(
            [fill(CALL, "buy", 2, 2.00, 0), fill(CALL, "sell", 2, 3.20, 90, trades.TAKE_PROFIT)]
        )
        self.assertEqual(still_open, [])
        t = trips[0]
        self.assertEqual(t["pnl"], 240.0)  # 2 * (3.20-2.00) * 100
        self.assertEqual(t["pnl_pct"], 60.0)
        self.assertEqual(t["multiplier"], 100)
        self.assertEqual(t["instrument"], "call")
        self.assertEqual(t["underlying"], "SPY")
        self.assertEqual(t["dte_at_entry"], 3)
        self.assertEqual(t["entry_hour"], 10)
        self.assertEqual(t["hold_minutes"], 90.0)
        self.assertEqual(t["exit_reason"], trades.TAKE_PROFIT)

    def test_stock_pnl_has_no_multiplier(self):
        trips, _ = trades.pair_round_trips([fill("AAPL", "buy", 10, 150.0, 0), fill("AAPL", "sell", 10, 151.5, 30)])
        self.assertEqual(trips[0]["pnl"], 15.0)
        self.assertEqual(trips[0]["instrument"], "stock")
        self.assertIsNone(trips[0]["dte_at_entry"])
        self.assertEqual(trips[0]["exit_reason"], trades.FLATTEN)  # no reason given

    def test_fifo_splits_lots_across_partial_sells(self):
        trips, still_open = trades.pair_round_trips(
            [fill(PUT, "buy", 3, 1.00, 0), fill(PUT, "buy", 2, 1.50, 10),
             fill(PUT, "sell", 4, 2.00, 20, trades.MODEL)]
        )
        self.assertEqual([t["qty"] for t in trips], [3, 1])
        self.assertEqual([t["entry_price"] for t in trips], [1.0, 1.5])
        self.assertEqual(still_open, [{"symbol": PUT, "qty": 1, "entry_price": 1.5, "entry_time": T0 + timedelta(minutes=10)}])

    def test_sell_without_lot_is_dropped_not_invented_as_short(self):
        trips, still_open = trades.pair_round_trips([fill(CALL, "sell", 1, 5.0, 0)])
        self.assertEqual(trips, [])
        self.assertEqual(still_open, [])

    def test_same_instant_buy_and_sell_pair_in_the_only_sensible_order(self):
        trips, _ = trades.pair_round_trips([fill(CALL, "sell", 1, 2.5, 0), fill(CALL, "buy", 1, 2.0, 0)])
        self.assertEqual(len(trips), 1)


class SummarizeTest(unittest.TestCase):
    def _trips(self):
        trips, _ = trades.pair_round_trips(
            [
                fill(CALL, "buy", 1, 2.00, 0), fill(CALL, "sell", 1, 3.00, 60, trades.TAKE_PROFIT),  # +100
                fill(PUT, "buy", 2, 1.00, 5), fill(PUT, "sell", 2, 0.60, 45, trades.STOP_LOSS),  # -80
                fill("AAPL", "buy", 10, 100.0, 10), fill("AAPL", "sell", 10, 101.0, 300),  # +10 flatten
            ]
        )
        return trips

    def test_headline_numbers(self):
        s = trades.summarize(self._trips())
        self.assertEqual(s["trades"], 3)
        self.assertEqual(s["pnl"], 30.0)
        self.assertAlmostEqual(s["win_rate_pct"], 66.7)
        self.assertEqual(s["profit_factor"], round(110 / 80, 2))

    def test_exit_reason_mix_covers_all_five_reasons(self):
        s = trades.summarize(self._trips())
        self.assertEqual(set(s["by_exit_reason"]), set(trades.EXIT_REASONS))
        self.assertEqual(s["by_exit_reason"][trades.STOP_LOSS]["trades"], 1)
        self.assertEqual(s["by_exit_reason"][trades.FLATTEN]["pnl"], 10.0)
        self.assertEqual(s["by_exit_reason"][trades.EXPIRY]["trades"], 0)

    def test_options_cuts(self):
        s = trades.summarize(self._trips())
        self.assertEqual(s["by_instrument"]["call"]["pnl"], 100.0)
        self.assertEqual(s["by_instrument"]["put"]["pnl"], -80.0)
        self.assertEqual(s["by_instrument"]["stock"]["trades"], 1)
        self.assertEqual(s["by_underlying"]["SPY"]["wins"], 1)
        self.assertEqual(s["by_dte_at_entry"]["3-7"]["trades"], 1)
        self.assertEqual(s["by_dte_at_entry"]["8-14"]["trades"], 1)
        self.assertEqual(s["by_dte_at_entry"]["stock"]["trades"], 1)
        self.assertEqual(s["by_entry_hour"]["10"]["trades"], 3)

    def test_profit_factor_none_when_nothing_lost(self):
        trips, _ = trades.pair_round_trips([fill(CALL, "buy", 1, 2.0, 0), fill(CALL, "sell", 1, 2.5, 5)])
        self.assertIsNone(trades.summarize(trips)["profit_factor"])

    def test_empty(self):
        s = trades.summarize([])
        self.assertEqual(s["trades"], 0)
        self.assertIsNone(s["win_rate_pct"])
        self.assertEqual(s["by_underlying"], {})


if __name__ == "__main__":
    unittest.main()

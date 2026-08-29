import unittest
from datetime import date

from bot import exits
from bot.models import Position

TODAY = date(2026, 9, 1)
CONFIG = {"stop_loss_pct": 40, "take_profit_pct": 60, "expiry_close_dte": 0}


def _opt(symbol="SPY260904C00770000", entry=2.0, current=2.0, qty=2):
    return Position(symbol, "option", qty, current * qty * 100, "SPY", entry, current)


def _stock(entry=100.0, current=100.0, qty=10):
    return Position("AAPL", "stock", qty, current * qty, None, entry, current)


class ExitReasonTest(unittest.TestCase):
    def test_no_exit_when_flat_and_far_from_expiry(self):
        self.assertIsNone(exits.exit_reason(_opt(), TODAY, CONFIG))

    def test_stop_loss_at_threshold(self):
        self.assertEqual(exits.exit_reason(_opt(entry=2.0, current=1.2), TODAY, CONFIG), exits.STOP_LOSS)

    def test_no_stop_just_above_threshold(self):
        self.assertIsNone(exits.exit_reason(_opt(entry=2.0, current=1.21), TODAY, CONFIG))

    def test_take_profit_at_threshold(self):
        self.assertEqual(exits.exit_reason(_opt(entry=2.0, current=3.2), TODAY, CONFIG), exits.TAKE_PROFIT)

    def test_expiry_day_closes_regardless_of_pnl(self):
        p = _opt(symbol="SPY260901C00770000", entry=2.0, current=2.1)  # expires today
        self.assertEqual(exits.exit_reason(p, TODAY, CONFIG), exits.EXPIRY)

    def test_expiry_close_dte_config_widens_the_window(self):
        p = _opt(symbol="SPY260902C00770000")  # 1 DTE
        self.assertIsNone(exits.exit_reason(p, TODAY, CONFIG))
        self.assertEqual(exits.exit_reason(p, TODAY, {**CONFIG, "expiry_close_dte": 1}), exits.EXPIRY)

    def test_expiry_beats_take_profit(self):
        p = _opt(symbol="SPY260901C00770000", entry=1.0, current=5.0)
        self.assertEqual(exits.exit_reason(p, TODAY, CONFIG), exits.EXPIRY)

    def test_missing_prices_cannot_trigger_price_rules_but_expiry_still_works(self):
        p = Position("SPY260904C00770000", "option", 1, 100.0, "SPY", None, None)
        self.assertIsNone(exits.exit_reason(p, TODAY, CONFIG))
        p_exp = Position("SPY260901C00770000", "option", 1, 100.0, "SPY", None, None)
        self.assertEqual(exits.exit_reason(p_exp, TODAY, CONFIG), exits.EXPIRY)

    def test_stock_uses_price_rules_and_never_expires(self):
        self.assertEqual(exits.exit_reason(_stock(100, 55), TODAY, CONFIG), exits.STOP_LOSS)
        self.assertIsNone(exits.exit_reason(_stock(100, 120), TODAY, CONFIG))

    def test_rules_disabled_when_config_omits_them(self):
        self.assertIsNone(exits.exit_reason(_opt(entry=2.0, current=0.1), TODAY, {"expiry_close_dte": 0}))

    def test_malformed_option_symbol_does_not_crash(self):
        p = Position("not-occ", "option", 1, 100.0, None, 2.0, 2.0)
        self.assertIsNone(exits.exit_reason(p, TODAY, CONFIG))


class CheckExitsTest(unittest.TestCase):
    def test_builds_full_size_sell_proposals_with_reasons(self):
        positions = {
            "keep": _opt(),
            "stop": _opt(symbol="QQQ260904P00700000", entry=3.0, current=1.5, qty=3),
            "exp": _opt(symbol="NVDA260901C00220000"),
        }
        positions["stop"].underlying = "QQQ"
        positions["exp"].underlying = "NVDA"

        proposals = exits.check_exits(positions, TODAY, CONFIG)

        by_symbol = {p.symbol: p for p in proposals}
        self.assertEqual(set(by_symbol), {"QQQ260904P00700000", "NVDA260901C00220000"})
        stop = by_symbol["QQQ260904P00700000"]
        self.assertEqual((stop.side, stop.qty, stop.instrument, stop.underlying), ("sell", 3, "option", "QQQ"))
        self.assertIn("stop_loss", stop.reason)
        self.assertIn("-50.0%", stop.reason)
        self.assertIn("expiry", by_symbol["NVDA260901C00220000"].reason)

    def test_empty_positions(self):
        self.assertEqual(exits.check_exits({}, TODAY, CONFIG), [])


if __name__ == "__main__":
    unittest.main()

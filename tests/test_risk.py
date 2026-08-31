import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from bot.models import AccountState, Position, Proposal
from bot import risk
from bot.risk import EASTERN, RiskManager

UNDERLYINGS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]


def make_config(**overrides):
    config = {
        "underlyings": UNDERLYINGS,
        "max_position_usd": 5000,
        "max_positions": 4,
        "max_contracts_per_order": 10,
        "daily_loss_cutoff_pct": 2.0,
        "min_days_to_expiration": 1,
        "max_days_to_expiration": 45,
        "trade_start": "09:45",
        "trade_end": "15:45",
        "last_entry": "15:15",
    }
    config.update(overrides)
    return config


class RiskManagerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.risk = RiskManager(make_config(), logs_dir=Path(self.tmpdir.name))
        self.account = AccountState(equity=100000, start_of_day_equity=100000, cash=100000)
        self.mid_session = datetime(2026, 1, 15, 12, 0)  # well inside the trading window


class CheckOrderBasicValidationTest(RiskManagerTestBase):
    def test_rejects_unknown_instrument(self):
        p = Proposal(instrument="bogus", symbol="AAPL", side="buy", qty=1)
        ok, reason = self.risk.check_order(p, self.account, 100, self.mid_session)
        self.assertFalse(ok)
        self.assertIn("instrument", reason)

    def test_rejects_unknown_side(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="hold", qty=1)
        ok, reason = self.risk.check_order(p, self.account, 100, self.mid_session)
        self.assertFalse(ok)
        self.assertIn("side", reason)

    def test_rejects_zero_qty(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=0)
        ok, _ = self.risk.check_order(p, self.account, 100, self.mid_session)
        self.assertFalse(ok)

    def test_rejects_negative_qty(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=-5)
        ok, _ = self.risk.check_order(p, self.account, 100, self.mid_session)
        self.assertFalse(ok)

    def test_rejects_zero_price(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        ok, _ = self.risk.check_order(p, self.account, 0, self.mid_session)
        self.assertFalse(ok)

    def test_rejects_symbol_not_in_whitelist(self):
        p = Proposal(instrument="stock", symbol="TSLA", side="buy", qty=1)
        ok, reason = self.risk.check_order(p, self.account, 100, self.mid_session)
        self.assertFalse(ok)
        self.assertIn("whitelist", reason)

    def test_option_whitelists_on_underlying_not_occ_symbol(self):
        p = Proposal(
            instrument="option",
            symbol="AAPL260204C00200000",
            side="buy",
            qty=1,
            underlying="AAPL",
        )
        ok, reason = self.risk.check_order(p, self.account, 2.0, self.mid_session)
        self.assertTrue(ok, reason)


class OptionsExpirationTest(RiskManagerTestBase):
    def test_rejects_option_expiring_same_day(self):
        p = Proposal(
            instrument="option", symbol="AAPL260115C00200000", side="buy", qty=1, underlying="AAPL"
        )
        ok, reason = self.risk.check_order(p, self.account, 2.0, self.mid_session)
        self.assertFalse(ok)
        self.assertIn("expiration", reason)

    def test_accepts_option_at_min_dte_boundary(self):
        p = Proposal(
            instrument="option", symbol="AAPL260116C00200000", side="buy", qty=1, underlying="AAPL"
        )  # dte == 1
        ok, reason = self.risk.check_order(p, self.account, 2.0, self.mid_session)
        self.assertTrue(ok, reason)

    def test_accepts_option_at_max_dte_boundary(self):
        p = Proposal(
            instrument="option", symbol="AAPL260301C00200000", side="buy", qty=1, underlying="AAPL"
        )  # dte == 45
        ok, reason = self.risk.check_order(p, self.account, 2.0, self.mid_session)
        self.assertTrue(ok, reason)

    def test_rejects_option_past_max_dte(self):
        p = Proposal(
            instrument="option", symbol="AAPL260302C00200000", side="buy", qty=1, underlying="AAPL"
        )  # dte == 46
        ok, reason = self.risk.check_order(p, self.account, 2.0, self.mid_session)
        self.assertFalse(ok)
        self.assertIn("expiration", reason)

    def test_rejects_option_qty_over_max_contracts_per_order(self):
        p = Proposal(
            instrument="option", symbol="AAPL260204C00200000", side="buy", qty=11, underlying="AAPL"
        )
        ok, reason = self.risk.check_order(p, self.account, 2.0, self.mid_session)
        self.assertFalse(ok)
        self.assertIn("max_contracts_per_order", reason)

    def test_accepts_option_qty_at_max_contracts_boundary(self):
        p = Proposal(
            instrument="option", symbol="AAPL260204C00200000", side="buy", qty=10, underlying="AAPL"
        )
        ok, reason = self.risk.check_order(p, self.account, 2.0, self.mid_session)  # notional 2000
        self.assertTrue(ok, reason)

    def test_rejects_malformed_option_symbol(self):
        p = Proposal(instrument="option", symbol="not-occ", side="buy", qty=1, underlying="AAPL")
        ok, _ = self.risk.check_order(p, self.account, 2.0, self.mid_session)
        self.assertFalse(ok)


class PositionCapTest(RiskManagerTestBase):
    def test_rejects_buy_exceeding_max_position_usd(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=100)
        ok, reason = self.risk.check_order(p, self.account, 60, self.mid_session)  # 6000 > 5000
        self.assertFalse(ok)
        self.assertIn("max_position_usd", reason)

    def test_accepts_buy_at_max_position_usd_boundary(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=50)
        ok, reason = self.risk.check_order(p, self.account, 100, self.mid_session)  # exactly 5000
        self.assertTrue(ok, reason)

    def test_existing_position_value_counts_toward_cap(self):
        account = AccountState(
            equity=100000,
            start_of_day_equity=100000,
            cash=100000,
            positions={"AAPL": Position(symbol="AAPL", instrument="stock", qty=10, market_value=4500)},
        )
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=10)
        ok, reason = self.risk.check_order(p, account, 100, self.mid_session)  # 4500+1000=5500
        self.assertFalse(ok)
        self.assertIn("max_position_usd", reason)

    def test_rejects_new_position_when_at_max_positions(self):
        positions = {
            sym: Position(symbol=sym, instrument="stock", qty=1, market_value=10)
            for sym in ["SPY", "QQQ", "MSFT", "NVDA"]
        }
        account = AccountState(
            equity=100000, start_of_day_equity=100000, cash=100000, positions=positions
        )
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        ok, reason = self.risk.check_order(p, account, 10, self.mid_session)
        self.assertFalse(ok)
        self.assertIn("max_positions", reason)

    def test_allows_adding_to_existing_position_even_at_max_positions(self):
        positions = {
            sym: Position(symbol=sym, instrument="stock", qty=1, market_value=10)
            for sym in ["AAPL", "SPY", "QQQ", "MSFT"]
        }
        account = AccountState(
            equity=100000, start_of_day_equity=100000, cash=100000, positions=positions
        )
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        ok, reason = self.risk.check_order(p, account, 10, self.mid_session)
        self.assertTrue(ok, reason)


class SellSideTest(RiskManagerTestBase):
    def test_rejects_sell_exceeding_held_qty(self):
        account = AccountState(
            equity=100000,
            start_of_day_equity=100000,
            cash=100000,
            positions={"AAPL": Position(symbol="AAPL", instrument="stock", qty=5, market_value=500)},
        )
        p = Proposal(instrument="stock", symbol="AAPL", side="sell", qty=10)
        ok, _ = self.risk.check_order(p, account, 100, self.mid_session)
        self.assertFalse(ok)

    def test_accepts_sell_at_held_qty_boundary(self):
        account = AccountState(
            equity=100000,
            start_of_day_equity=100000,
            cash=100000,
            positions={"AAPL": Position(symbol="AAPL", instrument="stock", qty=5, market_value=500)},
        )
        p = Proposal(instrument="stock", symbol="AAPL", side="sell", qty=5)
        ok, reason = self.risk.check_order(p, account, 100, self.mid_session)
        self.assertTrue(ok, reason)

    def test_rejects_sell_of_symbol_not_held(self):
        p = Proposal(instrument="stock", symbol="AAPL", side="sell", qty=1)
        ok, _ = self.risk.check_order(p, self.account, 100, self.mid_session)
        self.assertFalse(ok)

    def test_sell_allowed_past_last_entry_but_before_trade_end(self):
        past_last_entry = datetime(2026, 1, 15, 15, 30)  # after 15:15, before 15:45
        account = AccountState(
            equity=100000,
            start_of_day_equity=100000,
            cash=100000,
            positions={"AAPL": Position(symbol="AAPL", instrument="stock", qty=5, market_value=500)},
        )
        p = Proposal(instrument="stock", symbol="AAPL", side="sell", qty=5)
        ok, reason = self.risk.check_order(p, account, 100, past_last_entry)
        self.assertTrue(ok, reason)


class TradingWindowTest(RiskManagerTestBase):
    def test_rejects_buy_before_trade_start(self):
        early = datetime(2026, 1, 15, 9, 0)
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        ok, _ = self.risk.check_order(p, self.account, 100, early)
        self.assertFalse(ok)

    def test_rejects_buy_after_last_entry(self):
        late = datetime(2026, 1, 15, 15, 30)
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        ok, _ = self.risk.check_order(p, self.account, 100, late)
        self.assertFalse(ok)

    def test_accepts_buy_at_last_entry_boundary(self):
        boundary = datetime(2026, 1, 15, 15, 15)
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        ok, reason = self.risk.check_order(p, self.account, 100, boundary)
        self.assertTrue(ok, reason)

    def test_accepts_buy_at_trade_start_boundary(self):
        boundary = datetime(2026, 1, 15, 9, 45)
        p = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)
        ok, reason = self.risk.check_order(p, self.account, 100, boundary)
        self.assertTrue(ok, reason)

    def test_in_trading_window_true_at_trade_end_boundary(self):
        self.assertTrue(self.risk.in_trading_window(datetime(2026, 1, 15, 15, 45)))

    def test_in_trading_window_false_after_trade_end(self):
        self.assertFalse(self.risk.in_trading_window(datetime(2026, 1, 15, 15, 46)))


class HaltTest(RiskManagerTestBase):
    def test_not_halted_by_default(self):
        self.assertIsNone(self.risk.halted(self.mid_session))

    def test_manual_halt_file_halts(self):
        self.risk.manual_halt_file().touch()
        self.assertEqual(self.risk.halted(self.mid_session), "manual halt")

    def test_daily_halt_file_halts_for_that_day_only(self):
        self.risk.daily_halt_file(date(2026, 1, 15)).touch()
        self.assertEqual(self.risk.halted(datetime(2026, 1, 15, 12, 0)), "daily loss halt")
        self.assertIsNone(self.risk.halted(datetime(2026, 1, 16, 12, 0)))

    def test_global_halt_file_halts_every_account(self):
        challenger = RiskManager(make_config(), logs_dir=self.risk.logs_dir, account="qwen-a")
        self.assertEqual(self.risk.global_halt_file().name, "HALT")
        self.assertEqual(challenger.global_halt_file(), self.risk.global_halt_file())
        self.risk.global_halt_file().touch()
        self.assertEqual(self.risk.halted(self.mid_session), "global halt")
        self.assertEqual(challenger.halted(self.mid_session), "global halt")

    def test_both_halt_kinds_are_per_account(self):
        challenger = RiskManager(make_config(), logs_dir=self.risk.logs_dir, account="qwen-a")
        self.assertEqual(challenger.daily_halt_file(date(2026, 1, 15)).name, "HALT_qwen-a_2026-01-15")
        self.assertEqual(self.risk.daily_halt_file(date(2026, 1, 15)).name, "HALT_2026-01-15")
        self.assertEqual(challenger.manual_halt_file().name, "HALT_manual_qwen-a")
        self.assertEqual(self.risk.manual_halt_file().name, "HALT_manual")

        challenger.daily_halt_file(date(2026, 1, 15)).touch()
        self.assertEqual(challenger.halted(datetime(2026, 1, 15, 12, 0)), "daily loss halt")
        self.assertIsNone(self.risk.halted(datetime(2026, 1, 15, 12, 0)))  # official unaffected

    def test_challenger_kill_switch_never_halts_the_official_account(self):
        """The whole point of scoping the manual halt: a challenger's kill
        switch (CLI or the Home Assistant button) must not stop the judged
        account during the scoring window."""
        challenger = RiskManager(make_config(), logs_dir=self.risk.logs_dir, account="qwen-a")
        challenger.manual_halt_file().touch()
        self.assertEqual(challenger.halted(self.mid_session), "manual halt")
        self.assertIsNone(self.risk.halted(self.mid_session))

    def test_official_account_uses_unsuffixed_daily_halt(self):
        official = RiskManager(make_config(), logs_dir=self.risk.logs_dir, account="official")
        self.assertEqual(official.daily_halt_file(date(2026, 1, 15)).name, "HALT_2026-01-15")


class DailyLossBreachTest(RiskManagerTestBase):
    def test_not_breached_when_equity_up(self):
        account = AccountState(equity=101000, start_of_day_equity=100000, cash=0)
        self.assertFalse(self.risk.daily_loss_breached(account))

    def test_not_breached_just_under_cutoff(self):
        account = AccountState(equity=98010, start_of_day_equity=100000, cash=0)  # 1.99% loss
        self.assertFalse(self.risk.daily_loss_breached(account))

    def test_breached_at_exact_cutoff(self):
        account = AccountState(equity=98000, start_of_day_equity=100000, cash=0)  # exactly 2.0%
        self.assertTrue(self.risk.daily_loss_breached(account))

    def test_breached_beyond_cutoff(self):
        account = AccountState(equity=90000, start_of_day_equity=100000, cash=0)
        self.assertTrue(self.risk.daily_loss_breached(account))

    def test_zero_start_of_day_equity_does_not_breach(self):
        account = AccountState(equity=0, start_of_day_equity=0, cash=0)
        self.assertFalse(self.risk.daily_loss_breached(account))


if __name__ == "__main__":
    unittest.main()


class EarlyExitBlockTest(unittest.TestCase):
    """The churn guard (#132). On 2026-08-31 the judged account bought the
    same SPY put twice and market-sold it 10 and 20 minutes later on a ~9%
    adverse mark - the second time through prompt language forbidding
    exactly that. This is the mechanical version."""

    NOW = datetime(2026, 8, 31, 12, 0, tzinfo=EASTERN)

    def _pos(self, entry=2.44, current=2.27):
        return Position(symbol="SPY260902P00765000", instrument="option", qty=10,
                        market_value=2270, underlying="SPY",
                        avg_entry_price=entry, current_price=current)

    def _sell(self):
        return Proposal("option", "SPY260902P00765000", "sell", 10, underlying="SPY")

    def test_blocks_a_fresh_sell_on_a_small_drawdown(self):
        block = risk.early_exit_block(self._sell(), self._pos(),
                                      "2026-08-31T11:50:00-04:00", {}, self.NOW)
        self.assertIsNotNone(block)
        self.assertIn("min_hold_minutes", block)

    def test_allows_the_sell_once_the_hold_has_passed(self):
        self.assertIsNone(risk.early_exit_block(self._sell(), self._pos(),
                                                "2026-08-31T11:20:00-04:00", {}, self.NOW))

    def test_a_deep_drawdown_overrides_the_hold(self):
        """Headed for the stop anyway - blocking would only make the loss
        bigger for the sake of a rule."""
        pos = self._pos(entry=2.44, current=1.70)  # -30%
        self.assertIsNone(risk.early_exit_block(self._sell(), pos,
                                                "2026-08-31T11:55:00-04:00", {}, self.NOW))

    def test_buys_are_never_blocked(self):
        p = Proposal("option", "SPY260902P00765000", "buy", 10, underlying="SPY")
        self.assertIsNone(risk.early_exit_block(p, self._pos(),
                                                "2026-08-31T11:59:00-04:00", {}, self.NOW))

    def test_overnight_positions_are_sellable(self):
        """No entry today = held from a prior session; selling it is not
        churn, and the guard fails open."""
        self.assertIsNone(risk.early_exit_block(self._sell(), self._pos(), None, {}, self.NOW))

    def test_unknown_position_fails_open(self):
        self.assertIsNone(risk.early_exit_block(self._sell(), None,
                                                "2026-08-31T11:59:00-04:00", {}, self.NOW))

    def test_thresholds_come_from_config(self):
        cfg = {"min_hold_minutes": 5, "early_exit_drawdown_pct": 3}
        self.assertIsNone(risk.early_exit_block(self._sell(), self._pos(),
                                                "2026-08-31T11:50:00-04:00", cfg, self.NOW))
        pos = self._pos(current=2.42)  # -0.8% < 3%
        self.assertIsNotNone(risk.early_exit_block(self._sell(), pos,
                                                   "2026-08-31T11:58:00-04:00", cfg, self.NOW))

    def test_unparseable_entry_ts_fails_open(self):
        self.assertIsNone(risk.early_exit_block(self._sell(), self._pos(), "garbage", {}, self.NOW))

"""bot/holdings.py - what the model knows about its own positions (#170, #173)."""

import unittest

from bot import holdings
from bot.models import Position, Proposal

DAY = "2026-09-01"


def rec(ts, event, **fields):
    return {"ts": f"{DAY}T{ts}-04:00", "event": event, **fields}


def prediction(ts, **per_underlying):
    return rec(ts, "predictions", account="official", **per_underlying)


QQQ_PUT = "QQQ260903P00708000"
SPY_PUT = "SPY260908P00764000"

PRIOR_1200 = {"series": "KXNASDAQ100", "p_above_reference": 0.090, "p_up_over_1pct": 0.05, "p_down_over_1pct": 0.461,
              "suppressed": None, "chain": {"p_above_reference": 0.126, "p_up_over_1pct": 0.016, "p_down_over_1pct": 0.437, "suppressed": None}}
PRIOR_1220 = {"series": "KXNASDAQ100", "p_above_reference": 0.087, "p_up_over_1pct": 0.044, "p_down_over_1pct": 0.595,
              "suppressed": None, "chain": {"p_above_reference": 0.126, "p_up_over_1pct": 0.016, "p_down_over_1pct": 0.437, "suppressed": None}}


def position(symbol=QQQ_PUT, qty=4.0, entry=3.715, current=3.73):
    return {"symbol": symbol, "instrument": "option", "qty": qty, "market_value": qty * current * 100,
            "underlying": symbol[:3], "avg_entry_price": entry, "current_price": current}


class EntryContextTest(unittest.TestCase):
    def test_the_opener_is_the_first_buy_and_the_prior_is_the_one_before_it(self):
        """predictions is journaled before cycle_start, so 'the last
        predictions record before the order' is the same cycle's prior - not
        the later one that is closer in wall-clock time to now."""
        records = [
            prediction("12:00:03", QQQ=PRIOR_1200),
            rec("12:10:14", "order_submitted", side="buy", qty=4, symbol=QQQ_PUT, reason="QQQ down 1.2%; heavy downside odds"),
            prediction("12:20:02", QQQ=PRIOR_1220),
        ]
        ctx = holdings.entry_context([position()], records)[QQQ_PUT]
        self.assertEqual(ctx["reason"], "QQQ down 1.2%; heavy downside odds")
        self.assertEqual(ctx["opened_ts"], f"{DAY}T12:10:14-04:00")
        self.assertEqual(ctx["adds"], 0)
        self.assertEqual(ctx["prior_at_entry"]["kalshi"]["p_down_over_1pct"], 0.461)
        self.assertEqual(ctx["prior_at_entry"]["chain"]["p_above_reference"], 0.126)

    def test_later_buys_are_adds_not_a_new_opener(self):
        records = [
            rec("10:00:00", "order_submitted", side="buy", qty=2, symbol=QQQ_PUT, reason="first"),
            rec("10:30:00", "order_submitted", side="buy", qty=2, symbol=QQQ_PUT, reason="second"),
        ]
        ctx = holdings.entry_context([position()], records)[QQQ_PUT]
        self.assertEqual((ctx["reason"], ctx["adds"]), ("first", 1))

    def test_a_closed_and_rebought_symbol_gets_todays_reason(self):
        """Sells - the model's and code exits alike - bring the running
        quantity to zero, and the next buy is a fresh opener."""
        records = [
            rec("10:00:00", "order_submitted", side="buy", qty=4, symbol=QQQ_PUT, reason="monday"),
            rec("14:00:00", "order_submitted", side="sell", qty=4, symbol=QQQ_PUT, exit=True, reason="take_profit (+61%)"),
            rec("12:10:00", "order_submitted", side="buy", qty=4, symbol=QQQ_PUT, reason="tuesday"),
        ]
        ctx = holdings.entry_context([position()], records)[QQQ_PUT]
        self.assertEqual(ctx["reason"], "tuesday")

    def test_a_flatten_resets_the_symbol(self):
        records = [
            rec("10:00:00", "order_submitted", side="buy", qty=4, symbol=QQQ_PUT, reason="monday"),
            rec("15:50:00", "flatten", closed=[{"symbol": QQQ_PUT, "status": 200}]),
            rec("12:10:00", "order_submitted", side="buy", qty=4, symbol=QQQ_PUT, reason="tuesday"),
        ]
        self.assertEqual(holdings.entry_context([position()], records)[QQQ_PUT]["reason"], "tuesday")

    def test_no_opener_means_none_not_an_invented_thesis(self):
        records = [rec("10:00:00", "order_submitted", side="buy", qty=1, symbol=SPY_PUT, reason="other symbol")]
        self.assertIsNone(holdings.entry_context([position()], records)[QQQ_PUT])

    def test_prior_at_entry_is_none_when_no_prior_had_been_journaled(self):
        records = [rec("12:10:14", "order_submitted", side="buy", qty=4, symbol=QQQ_PUT, reason="r")]
        self.assertIsNone(holdings.entry_context([position()], records)[QQQ_PUT]["prior_at_entry"])


class RenderPositionsBlockTest(unittest.TestCase):
    def test_flat_says_so_in_terms_of_the_rule_it_implies(self):
        block = holdings.render_positions_block([], [], {}, {})
        self.assertIn("POSITIONS YOU HOLD: none - any sell would be a naked short", block)
        self.assertTrue(block.endswith("\n\n"))

    def test_a_position_shows_its_thesis_and_both_priors(self):
        ctx = {QQQ_PUT: {"opened_ts": f"{DAY}T12:10:14-04:00", "reason": "QQQ down 1.2%; heavy downside odds", "adds": 0,
                         "prior_at_entry": holdings.prior_for({"QQQ": PRIOR_1200}, "QQQ")}}
        block = holdings.render_positions_block([position()], [], ctx, {"QQQ": PRIOR_1220})
        self.assertIn("POSITIONS YOU HOLD (the ONLY symbols a sell may name - copy them exactly):", block)
        self.assertIn(f"- {QQQ_PUT} x4 @ 3.715, +0.4% vs entry; opened 12:10 ET", block)
        self.assertIn('stated at entry: "QQQ down 1.2%; heavy downside odds"', block)
        self.assertIn("prior at entry: Kalshi P(above) 0.090, P(up>1%) 0.050, P(down>1%) 0.461 | chain P(above) 0.126", block)
        self.assertIn("prior now:      Kalshi P(above) 0.087, P(up>1%) 0.044, P(down>1%) 0.595 | chain P(above) 0.126", block)

    def test_a_position_without_an_opener_says_no_recorded_thesis(self):
        block = holdings.render_positions_block([position()], [], {QQQ_PUT: None}, {})
        self.assertIn(f"- {QQQ_PUT} x4 @ 3.715, +0.4% vs entry; {holdings.NO_THESIS}", block)
        self.assertNotIn("prior at entry", block)

    def test_a_withheld_prior_is_named_as_withheld(self):
        withheld = dict(PRIOR_1200, suppressed="volume 120 < 250")
        ctx = {QQQ_PUT: {"opened_ts": f"{DAY}T12:10:14-04:00", "reason": "r", "adds": 0,
                         "prior_at_entry": holdings.prior_for({"QQQ": withheld}, "QQQ")}}
        block = holdings.render_positions_block([position()], [], ctx, {})
        self.assertIn("prior at entry: Kalshi withheld (volume 120 < 250) | chain P(above) 0.126", block)
        self.assertIn("prior now:      none", block)

    def test_no_prior_lines_for_a_name_without_a_prior(self):
        nvda = position("NVDA260904C00220000", qty=3, entry=2.5, current=2.0)
        ctx = {nvda["symbol"]: {"opened_ts": f"{DAY}T10:00:00-04:00", "reason": "r", "adds": 2, "prior_at_entry": None}}
        block = holdings.render_positions_block([nvda], [], ctx, {"QQQ": PRIOR_1220})
        self.assertIn("opened 10:00 ET (+2 adds since)", block)
        self.assertNotIn("prior", block)

    def test_resting_orders_render_and_an_unknown_lookup_is_said(self):
        order = {"id": "abc", "symbol": "QQQ260903P00709000", "side": "buy", "qty": 4.0, "limit_price": 3.56,
                 "submitted_at": "2026-09-01T16:00:17.1Z"}
        block = holdings.render_positions_block([], [order], {}, {})
        self.assertIn("RESTING ORDERS (sent, unfilled", block)
        self.assertIn("- buy 4 QQQ260903P00709000 @ limit 3.56, submitted 12:00 ET", block)
        self.assertIn("RESTING ORDERS: unknown this cycle", holdings.render_positions_block([], None, {}, {}))
        self.assertNotIn("RESTING", holdings.render_positions_block([], [], {}, {}))


HELD = {SPY_PUT: Position(symbol=SPY_PUT, instrument="option", qty=10, market_value=4990, underlying="SPY")}


class ResolveSellTest(unittest.TestCase):

    def _sell(self, symbol, side="sell", instrument="option"):
        return Proposal(instrument=instrument, symbol=symbol, side=side, qty=10, underlying=symbol[:3], reason="exit")

    def test_a_neighbouring_strike_resolves_to_the_held_contract(self):
        """The 2026-09-01 case: held 764, model wrote 763 / 763 / 762."""
        p, note = holdings.resolve_sell(self._sell("SPY260908P00763000"), HELD)
        self.assertEqual(p.symbol, SPY_PUT)
        self.assertEqual(p.reason, "exit")
        self.assertEqual(note, f"resolved SPY260908P00763000 -> {SPY_PUT} (the one SPY put held for 2026-09-08; only the strike differed)")

    def test_the_held_symbol_itself_is_untouched(self):
        p, note = holdings.resolve_sell(self._sell(SPY_PUT), HELD)
        self.assertEqual((p.symbol, note), (SPY_PUT, None))

    def test_a_different_expiry_or_type_does_not_resolve(self):
        for symbol in ("SPY260903P00763000", "SPY260908C00763000", "QQQ260908P00763000"):
            p, note = holdings.resolve_sell(self._sell(symbol), HELD)
            self.assertEqual((p.symbol, note), (symbol, None), symbol)

    def test_two_candidates_is_an_ambiguity_left_to_the_funnel(self):
        held = dict(HELD)
        held["SPY260908P00760000"] = Position(symbol="SPY260908P00760000", instrument="option", qty=5, market_value=2000, underlying="SPY")
        p, note = holdings.resolve_sell(self._sell("SPY260908P00763000"), held)
        self.assertEqual((p.symbol, note), ("SPY260908P00763000", None))

    def test_buys_and_stock_are_never_rewritten(self):
        p, note = holdings.resolve_sell(self._sell("SPY260908P00763000", side="buy"), HELD)
        self.assertEqual((p.symbol, note), ("SPY260908P00763000", None))
        p, note = holdings.resolve_sell(self._sell("SPY", instrument="stock"), HELD)
        self.assertEqual((p.symbol, note), ("SPY", None))


class HeldOnSameUnderlyingTest(unittest.TestCase):
    def test_names_the_options_held_on_that_underlying(self):
        held = {
            SPY_PUT: Position(symbol=SPY_PUT, instrument="option", qty=10, market_value=1, underlying="SPY"),
            QQQ_PUT: Position(symbol=QQQ_PUT, instrument="option", qty=4, market_value=1, underlying="QQQ"),
            "SPY": Position(symbol="SPY", instrument="stock", qty=3, market_value=1),
        }
        p = Proposal(instrument="option", symbol="SPY260908P00763000", side="sell", qty=10, underlying="SPY")
        self.assertEqual(holdings.held_on_same_underlying(p, held), [f"{SPY_PUT} x10"])


if __name__ == "__main__":
    unittest.main()

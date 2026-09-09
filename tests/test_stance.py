"""Stance changes: the measure that has a usable sample (#284).

Day 1 of the baseline run produced 3 account-days of P&L and 37 entries. The
P&L route needs ~25 trading days to resolve a 1%/day difference; the entry
rate resolves a 27% -> 15% shift in about five. These tests pin the counting
rules that decide what the rate means - especially the three exclusions,
each of which would otherwise let the number drift for reasons that have
nothing to do with the model.
"""

import unittest

from bot import stance

SPY_PUT = "SPY260916P00760000"
SPY_CALL = "SPY260916C00765000"
QQQ_PUT = "QQQ260916P00715000"
NVDA_CALL = "NVDA260916C00225000"


def entry(ts, symbol, side="buy", event="order_submitted", reason="r"):
    return {"ts": ts, "event": event, "side": side, "symbol": symbol, "reason": reason}


class DirectionTest(unittest.TestCase):
    def test_a_long_call_is_bullish_and_a_long_put_is_bearish(self):
        self.assertEqual(stance.direction_of(SPY_CALL, "buy"), stance.BULLISH)
        self.assertEqual(stance.direction_of(SPY_PUT, "buy"), stance.BEARISH)

    def test_selling_takes_no_stance(self):
        """An exit says the thesis is over, not that the opposite one has
        begun. Counting sells would turn every ordinary close-and-reassess
        into a reversal and the rate would measure churn we already have a
        name for."""
        self.assertIsNone(stance.direction_of(SPY_PUT, "sell"))
        self.assertIsNone(stance.direction_of(SPY_CALL, "sell"))

    def test_stock_and_junk_have_no_direction(self):
        self.assertIsNone(stance.direction_of("SPY", "buy"))
        self.assertIsNone(stance.direction_of("", "buy"))
        self.assertIsNone(stance.direction_of(SPY_CALL, None))


class StanceChangeTest(unittest.TestCase):
    def test_the_first_entry_on_an_underlying_reverses_nothing(self):
        self.assertIsNone(stance.stance_change(None, SPY_PUT, "buy", "2026-09-09T09:50:00-04:00", "r"))

    def test_adding_to_the_same_view_is_not_a_change(self):
        """base_a bought NVDA puts at 10:00 and again at 10:10. Doubling down
        is a different behaviour from turning round, and conflating them
        would inflate the rate on exactly the days the model was consistent."""
        prior = {"direction": stance.BEARISH, "symbol": SPY_PUT, "ts": "2026-09-09T10:00:00-04:00"}
        self.assertIsNone(stance.stance_change(prior, SPY_PUT, "buy", "2026-09-09T10:10:00-04:00", "r"))

    def test_opposite_type_on_the_same_underlying_is_a_change(self):
        """The real 10:10 divergence: base_a bought NVDA 225P while base_b
        bought NVDA 225C, same strike, same expiry, same config file."""
        prior = {"direction": stance.BEARISH, "symbol": "NVDA260916P00225000",
                 "ts": "2026-09-09T10:00:00-04:00", "reason": "downtrend"}
        change = stance.stance_change(prior, NVDA_CALL, "buy", "2026-09-09T10:10:00-04:00", "rebound")
        self.assertEqual(change["underlying"], "NVDA")
        self.assertEqual((change["from_direction"], change["to_direction"]), (stance.BEARISH, stance.BULLISH))
        self.assertEqual(change["minutes_since"], 10.0)
        self.assertEqual(change["from_reason"], "downtrend")

    def test_a_different_underlying_is_not_a_change(self):
        prior = {"direction": stance.BEARISH, "symbol": SPY_PUT, "ts": "2026-09-09T10:00:00-04:00"}
        self.assertIsNone(stance.stance_change(prior, NVDA_CALL, "buy", "2026-09-09T10:10:00-04:00", "r"))

    def test_an_unparseable_timestamp_costs_the_gap_not_the_flag(self):
        prior = {"direction": stance.BULLISH, "symbol": SPY_CALL, "ts": "not-a-time"}
        change = stance.stance_change(prior, SPY_PUT, "buy", "2026-09-09T10:10:00-04:00", "r")
        self.assertIsNotNone(change)
        self.assertIsNone(change["minutes_since"])


class RateTest(unittest.TestCase):
    def test_rejections_do_not_count(self):
        """base_a took 9 rejections on 2026-09-09 and base_b took 0, from one
        shared config.yaml. Counting proposals rather than fills would make
        the rate an artefact of the funnel, not of the model."""
        records = [
            entry("2026-09-09T09:50:00-04:00", SPY_PUT),
            entry("2026-09-09T10:00:00-04:00", SPY_CALL, event="order_rejected"),
            entry("2026-09-09T10:10:00-04:00", SPY_CALL, event="dry_run"),
        ]
        summary = stance.rate(records)
        self.assertEqual(summary["entries"], 1)
        self.assertEqual(summary["changes"], 0)

    def test_the_denominator_excludes_first_sightings(self):
        """Three underlyings touched once each can produce no reversal, so
        counting them in the denominator would report 0% for a day on which
        nothing could have gone either way."""
        records = [
            entry("2026-09-09T09:50:00-04:00", SPY_PUT),
            entry("2026-09-09T10:00:00-04:00", QQQ_PUT),
            entry("2026-09-09T10:10:00-04:00", NVDA_CALL),
        ]
        summary = stance.rate(records)
        self.assertEqual(summary["entries"], 3)
        self.assertEqual(summary["reversible"], 0)
        self.assertIsNone(summary["pct"])

    def test_the_real_base_a_qqq_oscillation(self):
        """put -> call -> put on QQQ inside forty minutes: two changes out of
        two reversible entries, from three entries total."""
        records = [
            entry("2026-09-09T10:40:00-04:00", QQQ_PUT),
            entry("2026-09-09T11:00:00-04:00", "QQQ260916C00725000"),
            entry("2026-09-09T11:20:00-04:00", QQQ_PUT),
        ]
        summary = stance.rate(records)
        self.assertEqual((summary["entries"], summary["reversible"], summary["changes"]), (3, 2, 2))
        self.assertEqual(summary["pct"], 100.0)
        self.assertEqual(len(summary["details"]), 2)

    def test_describe_says_the_denominator_out_loud(self):
        """'3 stance changes' means different things on 18 cycles and 36."""
        records = [
            entry("2026-09-09T10:40:00-04:00", QQQ_PUT),
            entry("2026-09-09T11:00:00-04:00", "QQQ260916C00725000"),
        ]
        text = stance.describe(stance.rate(records))
        self.assertIn("1 stance change(s)", text)
        self.assertIn("1 reversible", text)

    def test_a_quiet_day_describes_itself_without_dividing_by_zero(self):
        self.assertIn("no option entries", stance.describe(stance.rate([])))
        self.assertIn("none on an underlying", stance.describe(stance.rate([entry("t", SPY_PUT)])))


if __name__ == "__main__":
    unittest.main()

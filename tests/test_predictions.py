import pathlib
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


def settled(close, value, ticker="S"):
    return {"ticker": ticker, "status": "settled", "close_time": close, "expiration_value": value}


class LatestSettlementTest(unittest.TestCase):
    """The reference close must be chosen by date, never by position.

    The shape in the first test is the one observed live on 2026-08-31: Kalshi
    returned Thursday's settled event ahead of Friday's, so taking the first row
    with an expiration_value picked a close that was a day stale and 0.25% too
    high. Every probability derived from it inherited the error.
    """

    def test_picks_the_newest_close_not_the_first_row(self):
        rows = [settled("2026-08-27T20:00:00Z", "7730.99"),
                settled("2026-08-26T20:00:00Z", "7675.70"),
                settled("2026-08-28T20:00:00Z", "7711.76")]
        self.assertEqual(predictions.latest_settlement(rows, NOW), 7711.76)

    def test_ignores_rows_with_no_expiration_value(self):
        rows = [settled("2026-08-28T20:00:00Z", None),
                settled("2026-08-27T20:00:00Z", "7730.99")]
        self.assertEqual(predictions.latest_settlement(rows, NOW), 7730.99)

    def test_ignores_a_malformed_close_time(self):
        rows = [settled("not-a-date", "9999.99"), settled("2026-08-28T20:00:00Z", "7711.76")]
        self.assertEqual(predictions.latest_settlement(rows, NOW), 7711.76)

    def test_ignores_a_close_time_still_ahead(self):
        """Whatever the status field says, a market that has not closed yet
        cannot be the previous close."""
        rows = [settled("2026-09-04T20:00:00Z", "8000.00"), settled("2026-08-28T20:00:00Z", "7711.76")]
        self.assertEqual(predictions.latest_settlement(rows, NOW), 7711.76)

    def test_withholds_a_stale_reference(self):
        """A wrong yardstick is worse than none: the probabilities still render
        and still look authoritative."""
        self.assertIsNone(predictions.latest_settlement([settled("2026-08-01T20:00:00Z", "7500")], NOW))

    def test_a_long_weekend_still_counts(self):
        """Thursday's close before a Friday holiday is four days back and is
        the legitimate reference, so the staleness guard must not eat it."""
        self.assertEqual(predictions.latest_settlement([settled("2026-08-27T20:00:00Z", "7730.99")], NOW), 7730.99)

    def test_nothing_settled(self):
        self.assertIsNone(predictions.latest_settlement([], NOW))


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
        self.assertIn("PRIORS to weigh, not signals to copy", block)
        # The pair is the point: the header must tell the model what
        # disagreement between the two crowds means.
        self.assertIn("DISAGREEMENT", block)
        # #172: the model restated these numbers wrongly, skewed toward its
        # trade, three times in one day. Quote or name, never restate.
        self.assertIn("quote it exactly as printed or refer to it by name", block)
        self.assertIn("audited against this block", block)


class UsabilityGateTest(unittest.TestCase):
    """A barely-traded range market still quotes every bucket. Normalising
    those midpoints yields a flat distribution that looks authoritative and
    says nothing - measured live on 2026-08-30, SPY implied a 64% chance of a
    >1% move in one session on 70 contracts of volume."""

    def summary(self, markets=TODAY, reference=7440.0):
        s = predictions.summarize_range_event(markets, reference=reference)
        s["series"] = "KXINX"
        return s

    def test_a_real_distribution_passes(self):
        s = self.summary()

        self.assertLessEqual(s["flatness"], predictions.MAX_FLATNESS)
        self.assertIsNone(predictions.unusable_reason(s))

    def test_thin_volume_is_suppressed(self):
        s = self.summary()
        s["volume"] = 70.0

        self.assertIn("thin", predictions.unusable_reason(s))

    def test_flat_distribution_is_suppressed_however_much_volume(self):
        flat = [market("E-FLAT", lo, lo + 24.99, 0.19, 0.21, volume=100_000)
                for lo in range(7300, 7600, 25)]
        s = predictions.summarize_range_event(flat, reference=7440.0)

        # Every bucket priced the same: normalising cannot rescue it.
        self.assertGreater(s["flatness"], predictions.MAX_FLATNESS)
        self.assertIn("flat", predictions.unusable_reason(s))

    def test_thresholds_come_from_config(self):
        s = self.summary()
        s["volume"] = 70.0

        self.assertIsNone(predictions.unusable_reason(s, {"predictions_min_volume": 10}))

    def test_a_suppressed_prior_never_reaches_the_model(self):
        s = self.summary()
        s["suppressed"] = "thin: volume 70.0 < 250.0"

        self.assertEqual(predictions.prompt_block({"SPY": s}), "")

    def test_one_usable_underlying_still_renders(self):
        good, bad = self.summary(), self.summary()
        bad["suppressed"] = "thin: volume 1 < 250.0"
        block = predictions.prompt_block({"SPY": good, "QQQ": bad})

        self.assertIn("SPY via", block)
        self.assertNotIn("QQQ via", block)


class JournalFieldsTest(unittest.TestCase):
    """The prompt is not journaled - it carries the whole option chain - so
    without this there is no way to ask what second opinion the model had."""

    def test_empty_when_nothing(self):
        self.assertEqual(predictions.journal_fields({}), {})

    def test_records_the_numbers_and_the_verdict(self):
        s = predictions.summarize_range_event(TODAY, reference=7440.0)
        s["series"] = "KXINX"
        s["suppressed"] = "thin: volume 70.0 < 250.0"
        fields = predictions.journal_fields({"SPY": s})["SPY"]

        self.assertEqual(fields["series"], "KXINX")
        self.assertEqual(fields["p_above_reference"], s["p_above_reference"])
        self.assertEqual(fields["flatness"], s["flatness"])
        # A withheld prior is recorded WITH its reason, so "no second opinion
        # today" is answerable rather than an absence.
        self.assertEqual(fields["suppressed"], "thin: volume 70.0 < 250.0")

    def test_shown_prior_records_no_suppression(self):
        s = predictions.summarize_range_event(TODAY, reference=7440.0)
        s["series"] = "KXINX"

        self.assertIsNone(predictions.journal_fields({"SPY": s})["SPY"]["suppressed"])


class CycleJournalsThePriorTest(unittest.TestCase):
    def test_run_cycle_logs_the_predictions_event(self):
        source = (pathlib.Path(__file__).resolve().parent.parent / "run_cycle.py").read_text()

        self.assertIn('journal.log("predictions"', source)
        self.assertIn("predictions.journal_fields", source)


if __name__ == "__main__":
    unittest.main()


def call_ladder(strikes_survival, expiry="260902"):
    """Contracts whose adjacent call-mid differences imply the given
    survival probabilities: C is built backwards so -dC/dK at each midpoint
    is exactly the number the test names."""
    strikes = [k for k, _ in strikes_survival]
    ps = [p for _, p in strikes_survival[:-1]]
    # C[i] = C[i+1] + p_i * (K[i+1]-K[i]), built back to front.
    cs = [0.0] * len(strikes)
    cs[-1] = 0.50
    for i in range(len(strikes) - 2, -1, -1):
        cs[i] = cs[i + 1] + ps[i] * (strikes[i + 1] - strikes[i])
    out = {}
    for k, c in zip(strikes, cs):
        occ = f"SPY{expiry}C{int(k * 1000):08d}"
        out[occ] = {"latestQuote": {"bp": round(c - 0.02, 4), "ap": round(c + 0.02, 4)}}
    return out


class ChainSummaryTest(unittest.TestCase):
    """P(S>K) = -dC/dK (#140). Idea credit: greatfriend#8857 (Discord)."""

    LADDER = ((760, 0.95), (762, 0.85), (764, 0.65), (766, 0.45), (768, 0.25), (770, 0.10), (772, None))

    def test_reads_the_probabilities_the_prices_imply(self):
        out = predictions.chain_summary(call_ladder(self.LADDER), reference=765.0)
        self.assertIsNone(out["suppressed"])
        self.assertEqual(out["expiry"], "2026-09-02")
        # Survival lives at strike-pair midpoints: the (764,766) pair's
        # increment is the ladder's 0.65, carried at 765 exactly.
        self.assertAlmostEqual(out["p_above_reference"], 0.65, places=2)
        self.assertGreater(out["p_above_reference"], out["p_up_over_1pct"])
        # Crosses 0.5 between 765 (0.65) and 767 (0.45) -> 766.5.
        self.assertAlmostEqual(out["implied_median"], 766.5, places=1)

    def test_puts_and_one_sided_quotes_are_ignored(self):
        contracts = call_ladder(self.LADDER)
        contracts["SPY260902P00765000"] = {"latestQuote": {"bp": 2.0, "ap": 2.1}}
        contracts["SPY260902C00774000"] = {"latestQuote": {"bp": 0, "ap": 0.05}}
        out = predictions.chain_summary(contracts, reference=765.0)
        self.assertEqual(out["strikes"], len(self.LADDER))

    def test_nearest_expiry_wins(self):
        near = call_ladder(self.LADDER, expiry="260902")
        far = call_ladder([(700, 0.9), (720, 0.7), (740, 0.5), (760, 0.3), (780, 0.1), (800, None)], expiry="260911")
        out = predictions.chain_summary({**far, **near}, reference=765.0)
        self.assertEqual(out["expiry"], "2026-09-02")

    def test_too_few_strikes_is_suppressed(self):
        out = predictions.chain_summary(call_ladder(self.LADDER[:4]), reference=765.0)
        self.assertIn("thin", out["suppressed"])

    def test_a_noisy_curve_is_suppressed_not_shown(self):
        """Crossed/wide quotes make C rise with strike; clamping more than a
        third of the increments means the curve is noise wearing a
        distribution - the chain's flatness-equivalent gate."""
        noisy = {}
        for i, k in enumerate(range(760, 774, 2)):
            c = 3.0 + (0.5 if i % 2 else 0.0)  # sawtooth: half the diffs negative
            noisy[f"SPY260902C{int(k * 1000):08d}"] = {"latestQuote": {"bp": c - 0.02, "ap": c + 0.02}}
        out = predictions.chain_summary(noisy, reference=765.0)
        self.assertIn("noisy", out["suppressed"])

    def test_no_calls_at_all_is_none(self):
        self.assertIsNone(predictions.chain_summary({}, reference=765.0))

    def test_without_reference_still_gives_median(self):
        out = predictions.chain_summary(call_ladder(self.LADDER), reference=None)
        self.assertIsNone(out["suppressed"])
        self.assertNotIn("p_above_reference", out)
        self.assertIsNotNone(out["implied_median"])


class ChainInPromptTest(unittest.TestCase):
    def _kalshi(self, suppressed=None):
        s = predictions.summarize_range_event(TODAY, reference=7440.0)
        s["series"] = "KXINX"
        s["suppressed"] = suppressed
        return s

    def _chain(self):
        return predictions.chain_summary(
            call_ladder(ChainSummaryTest.LADDER), reference=765.0)

    def test_both_crowds_render_side_by_side(self):
        entry = self._kalshi(); entry["chain"] = self._chain()
        block = predictions.prompt_block({"SPY": entry})
        self.assertIn("SPY via KXINX", block)
        self.assertIn("SPY via option chain", block)

    def test_chain_survives_a_suppressed_kalshi(self):
        """The #111 payoff: on the days the event market is withheld the
        model still gets one crowd estimate."""
        entry = self._kalshi(suppressed="thin: volume 45 < 250")
        entry["chain"] = self._chain()
        block = predictions.prompt_block({"SPY": entry})
        self.assertNotIn("SPY via KXINX", block)
        self.assertIn("SPY via option chain", block)

    def test_chain_only_entry_renders(self):
        block = predictions.prompt_block({"SPY": {"chain": self._chain()}})
        self.assertIn("SPY via option chain", block)

    def test_suppressed_chain_stays_out_of_the_prompt_but_in_the_journal(self):
        chain = predictions.chain_summary(call_ladder(ChainSummaryTest.LADDER[:4]), reference=765.0)
        entry = self._kalshi(); entry["chain"] = chain
        block = predictions.prompt_block({"SPY": entry})
        self.assertNotIn("SPY via option chain", block)
        fields = predictions.journal_fields({"SPY": entry})
        self.assertIn("thin", fields["SPY"]["chain"]["suppressed"])

"""bot/citations.py - does a reason quote numbers the prompt contained? (#172)

The priors below are the judged account's journaled 12:00 and 12:20 Eastern
priors from 2026-09-01, used as fixtures. The quoted figures in the reasons
are the model's real 13:00 and 13:20 phrasing - which, against the priors
of THEIR OWN cycles, were exact; here they are deliberately paired with the
wrong hour to exercise the unsupported path.
"""

import unittest

from bot import citations
from bot.models import Proposal

PRIOR_1200 = {
    "QQQ": {"series": "KXNASDAQ100", "p_above_reference": 0.126, "p_up_over_1pct": 0.042, "p_down_over_1pct": 0.338,
            "implied_move_pct": -0.61, "suppressed": None,
            "chain": {"p_above_reference": 0.14, "p_up_over_1pct": 0.02, "p_down_over_1pct": 0.29, "implied_move_pct": -0.3, "suppressed": None}},
    "SPY": {"series": "KXINX", "p_above_reference": 0.573, "p_up_over_1pct": 0.049, "p_down_over_1pct": 0.121, "suppressed": None},
}
PRIOR_1220 = {"QQQ": {"series": "KXNASDAQ100", "p_above_reference": 0.087, "p_up_over_1pct": 0.044, "p_down_over_1pct": 0.595, "suppressed": None,
                      "chain": {"p_above_reference": 0.126, "p_up_over_1pct": 0.016, "p_down_over_1pct": 0.437, "suppressed": None}}}


def proposal(reason, symbol="QQQ260903P00708000"):
    return Proposal(instrument="option", symbol=symbol, side="buy", qty=4, underlying="QQQ", reason=reason)


class ExtractClaimsTest(unittest.TestCase):
    def test_percentages_in_a_prior_clause_are_claims(self):
        claims = citations.extract_claims("Kalshi shows a 68.7% chance of down>1% close and only 7.6% chance of finishing above prior close")
        self.assertEqual(claims, [("68.7%", 0.687), ("7.6%", 0.076)])

    def test_price_moves_and_pnl_are_not_claims(self):
        self.assertEqual(citations.extract_claims("QQQ down 1.2% intraday; profitable small winner at +32% vs entry"), [])

    def test_the_labels_own_1pct_is_not_a_claim(self):
        """'P(down>1%)' contains '1%' - the label is not a quoted figure."""
        self.assertEqual(citations.extract_claims("crowd via Kalshi shows extreme bearishness (81.9% down>1%)"), [("81.9%", 0.819)])
        self.assertEqual(citations.extract_claims("Kalshi P(down>1%) supports this"), [])

    def test_fractions_count_too(self):
        self.assertEqual(citations.extract_claims("Kalshi P(above) 0.087 and chain 0.126 agree"), [("0.087", 0.087), ("0.126", 0.126)])

    def test_a_delta_clause_is_a_menu_number_not_a_prior(self):
        self.assertEqual(citations.extract_claims("delta 0.48 is roughly a 48% chance of finishing in the money"), [])

    def test_clauses_are_judged_separately(self):
        claims = citations.extract_claims("Kalshi P(down>1%) 59.5% is the prior; the put is +32% vs entry, a 40% stop protects it")
        self.assertEqual(claims, [("59.5%", 0.595)])


class PriorValuesTest(unittest.TestCase):
    def test_both_crowds_both_underlyings_and_complements(self):
        values = dict(citations.prior_values(PRIOR_1200))
        self.assertEqual(values["QQQ Kalshi P(down>1%)"], 0.338)
        self.assertEqual(values["QQQ chain P(above)"], 0.14)
        self.assertEqual(values["SPY Kalshi P(above)"], 0.573)
        self.assertAlmostEqual(values["1 - QQQ Kalshi P(above)"], 0.874)
        self.assertAlmostEqual(values["QQQ Kalshi implied move"], 0.0061)

    def test_a_withheld_source_was_never_shown_so_it_does_not_count(self):
        prior = {"QQQ": dict(PRIOR_1200["QQQ"], suppressed="volume 120 < 250")}
        labels = [label for label, _ in citations.prior_values(prior)]
        self.assertFalse(any("Kalshi" in label for label in labels))
        self.assertTrue(any("chain" in label for label in labels))

    def test_journal_envelope_keys_are_skipped(self):
        record = {"ts": "t", "event": "predictions", "account": "official", **PRIOR_1220}
        self.assertTrue(all(label.startswith(("QQQ", "1 - QQQ")) for label, _ in citations.prior_values(record)))


class AuditTest(unittest.TestCase):
    def test_figures_that_match_nothing_are_unsupported(self):
        result = citations.audit([proposal("QQQ down 1.2% intraday; Kalshi shows a 68.7% chance of down>1% close and only 7.6% chance of finishing above prior close")], PRIOR_1200)
        self.assertEqual(result["checked"], 2)
        self.assertEqual([u["quoted"] for u in result["unsupported"]], ["68.7%", "7.6%"])
        # The nearest real number is named so a reader can see how far off the
        # quote was - and it is never within tolerance, or it would be support.
        for u, quoted in zip(result["unsupported"], (0.687, 0.076), strict=True):
            self.assertGreater(abs(u["nearest"]["value"] - quoted), citations.TOLERANCE, u)
        self.assertEqual(result["unsupported"][0]["nearest"], {"label": "1 - QQQ chain P(down>1%)", "value": 0.71})

    def test_a_single_unsupported_figure_names_the_nearest_real_one(self):
        result = citations.audit([Proposal("option", "QQQ260903P00708000", "sell", 4, underlying="QQQ",
                                           reason="crowd via Kalshi shows extreme bearishness (81.9% down>1%)")], PRIOR_1220)
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["unsupported"][0]["quoted"], "81.9%")
        self.assertEqual(result["unsupported"][0]["nearest"], {"label": "1 - QQQ chain P(above)", "value": 0.874})

    def test_another_underlyings_figure_quoted_as_this_ones_is_misattributed(self):
        """The real 13:20 case: QQQ's chain prior was withheld that cycle and
        the model quoted SPY's chain P(down>1%) 0.273 as "the options market"
        for QQQ. A real number, wrong name - reported, not counted as invented."""
        prior = {"QQQ": {"series": "KXNASDAQ100", "p_above_reference": 0.068, "p_up_over_1pct": 0.044, "p_down_over_1pct": 0.819,
                         "suppressed": None, "chain": {"p_above_reference": None, "suppressed": "noisy"}},
                 "SPY": {"series": "KXINX", "p_above_reference": 0.108, "p_up_over_1pct": 0.05, "p_down_over_1pct": 0.121, "suppressed": None,
                         "chain": {"p_above_reference": 0.173, "p_up_over_1pct": 0.007, "p_down_over_1pct": 0.273, "suppressed": None}}}
        p = Proposal("option", "QQQ260903P00708000", "sell", 4, underlying="QQQ",
                     reason="crowd via Kalshi shows extreme bearishness (81.9% down>1%) but options market disagrees with only 27.3% P(down>1%)")
        result = citations.audit([p], prior)
        self.assertEqual((result["checked"], result["unsupported"]), (2, []))
        self.assertEqual([(u["quoted"], u["nearest"]["label"]) for u in result["misattributed"]], [("27.3%", "SPY chain P(down>1%)")])
        self.assertIn("1 misattributed - QQQ260903P00708000 quoted 27.3% which is SPY chain P(down>1%)", citations.describe(result))

    def test_a_figure_matching_the_own_underlying_is_not_misattributed_even_if_another_matches_too(self):
        prior = {"QQQ": {"series": "x", "p_above_reference": 0.10, "suppressed": None},
                 "SPY": {"series": "y", "p_above_reference": 0.10, "suppressed": None}}
        result = citations.audit([proposal("Kalshi P(above) is 10% for QQQ")], prior)
        self.assertEqual((result["unsupported"], result["misattributed"]), ([], []))

    def test_an_honest_citation_is_supported_within_rounding(self):
        result = citations.audit([proposal("Kalshi P(down>1%) at 59.5% and the chain's 44% both say down")], PRIOR_1220)
        self.assertEqual((result["checked"], result["unsupported"]), (2, []))

    def test_a_complement_is_supported(self):
        result = citations.audit([proposal("Kalshi gives a 91% chance of closing below the prior close")], PRIOR_1220)
        self.assertEqual(result["unsupported"], [])

    def test_no_prior_means_nothing_to_audit(self):
        self.assertIsNone(citations.audit([proposal("Kalshi says 80%")], {}))
        self.assertIsNone(citations.audit([proposal("Kalshi says 80%")], None))

    def test_research_tools_make_the_audit_inconclusive(self):
        result = citations.audit([proposal("Kalshi says 80%")], PRIOR_1220, tool_calls=[{"name": "get_stock_bars"}])
        self.assertEqual(result, {"skipped": "research tools ran - a quoted figure may come from a tool result"})

    def test_a_hold_has_nothing_to_check(self):
        self.assertEqual(citations.audit([], PRIOR_1220), {"checked": 0, "unsupported": [], "misattributed": []})

    def test_describe(self):
        result = citations.audit([proposal("Kalshi shows 68.7% chance of down>1% close")], PRIOR_1220)
        self.assertEqual(citations.describe(result),
                         "prior citations: 1 checked, 1 unsupported, 0 misattributed - QQQ260903P00708000 quoted 68.7% (nearest QQQ Kalshi P(down>1%) 0.595)")
        self.assertEqual(citations.describe(None), "prior citations: nothing to audit")


SPY_PUT = "SPY260908P00760000"


def sell(reason, symbol=SPY_PUT, underlying="SPY", side="sell"):
    return Proposal(instrument="option", symbol=symbol, side=side, qty=10, underlying=underlying, reason=reason)


class ExitClaimsTest(unittest.TestCase):
    """#188 (godmagick's find): the 2026-09-01 SPY put exit, replayed. All
    four of that trade's reasons carried a forced-close/backstop claim at 7
    DTE, and the filled one claimed the market "held above prior close"
    while SPY sat 0.73% below it."""

    CONFIG = {"eod_close_dte": 1, "expiry_close_dte": 0}
    DTES = {SPY_PUT: 7}
    SPOT_REF = {"SPY": (761.28, 766.87)}  # spot BELOW prior close

    def audit(self, *proposals):
        return citations.audit_exit_claims(list(proposals), self.DTES, self.SPOT_REF, self.CONFIG)

    def test_the_four_real_reasons_are_all_flagged_as_fabricated_urgency(self):
        reasons = [
            "closing to cut loss before forced expiry sale and free slot for better setup",
            "taking loss now frees capital and avoids theta decay on a losing thesis",
            "cut loss before forced close tomorrow and free slot for better setup",
            "forced close approaches with 7 DTE remaining - cut loss before backstop forces exit",
        ]
        flags = [f for r in reasons for f in self.audit(sell(r)) if f["kind"] == "fabricated_urgency"]
        # The second reason cites only theta - arguable rhetoric, deliberately unflagged.
        self.assertEqual(len(flags), 3)
        self.assertIn("7 DTE - no code exit for 6 more day(s)", flags[0]["fact"])

    def test_the_filled_exits_direction_claim_is_flagged(self):
        flags = self.audit(sell("thesis failed as market held above prior close despite bearishness"))
        self.assertEqual([f["kind"] for f in flags], ["wrong_direction"])
        self.assertIn("SPY 761.28 is below prior close 766.87", flags[0]["fact"])

    def test_a_true_direction_claim_is_not_flagged(self):
        self.assertEqual(self.audit(sell("SPY stayed below prior close all afternoon")), [])

    def test_a_genuinely_expiring_contract_may_cite_the_backstop(self):
        flags = citations.audit_exit_claims([sell("backstop closes this today")],
                                            {SPY_PUT: 1}, self.SPOT_REF, self.CONFIG)
        self.assertEqual([f for f in flags if f["kind"] == "fabricated_urgency"], [])

    def test_buys_are_never_urgency_flagged(self):
        p = sell("7-DTE put gives room for thesis to play out before forced close", side="buy")
        self.assertEqual([f for f in self.audit(p) if f["kind"] == "fabricated_urgency"], [])

    def test_unknown_dte_or_spot_is_not_guessed_at(self):
        flags = citations.audit_exit_claims([sell("cut loss before forced close")], {}, {}, self.CONFIG)
        self.assertEqual(flags, [])

    def test_describe_exit_claims_names_the_quoted_phrase_and_the_fact(self):
        line = citations.describe_exit_claims(self.audit(sell("exit before backstop forces sale")))
        self.assertIn("exit claims contradicted by the account: 1", line)
        self.assertIn("backstop", line)


if __name__ == "__main__":
    unittest.main()

import unittest

from bot import review

DAY = "2026-09-01"


def rec(ts, event, **fields):
    return {"ts": f"{DAY}T{ts}-04:00", "event": event, **fields}


RECORDS = [
    rec("09:50:00", "cycle_start", equity=100000.0, day_pnl=0.0),
    rec("09:50:01", "config", config_hash="aaaa", config_file="config.yaml", model="m1", overrides={}),
    rec("09:50:05", "decision", raw="[]", count=0, model="m1", usage={"prompt_tokens": 5000, "completion_tokens": 2},
        latency_sec=1.2, finish_reason="stop"),
    rec("10:00:00", "cycle_start", equity=100200.0, day_pnl=200.0),
    rec("10:00:01", "config", config_hash="aaaa", config_file="config.yaml", model="m1", overrides={}),
    rec("10:00:05", "decision", raw='[{"symbol":"SPY..."}]', count=2, model="m1",
        usage={"prompt_tokens": 5100, "completion_tokens": 40}, latency_sec=2.0, finish_reason="stop"),
    rec("10:00:06", "order_submitted", side="buy", qty=2, symbol="SPY260904C00770000", order_id="o1", reason="momentum"),
    rec("10:00:07", "order_rejected", side="buy", qty=1, symbol="TSLA", detail="TSLA not in underlyings whitelist"),
    rec("10:10:00", "cycle_start", equity=100150.0, day_pnl=150.0),
    rec("10:10:01", "config", config_hash="bbbb", config_file="config.yaml", model="m1", overrides={"temperature": {"value": 0.5}}),
    rec("10:10:02", "order_submitted", side="sell", qty=2, symbol="SPY260904C00770000", order_id="o2",
        reason="take_profit (+61.0% vs entry)", exit=True),
    rec("10:10:03", "order_rejected", side="buy", qty=1, symbol="TSLA", detail="TSLA not in underlyings whitelist"),
    rec("10:20:00", "error", where="decide", detail="ReadTimeout"),
    rec("10:30:00", "decision", raw="", count=0, model="m1", usage={"prompt_tokens": 5000, "completion_tokens": 800},
        latency_sec=9.0, finish_reason="length"),
]


class EquityFactsTest(unittest.TestCase):
    def test_open_close_and_pnl_from_cycle_starts(self):
        e = review.equity_facts(RECORDS)
        self.assertEqual(e["cycles"], 3)
        self.assertEqual(e["equity_open"], 100000.0)
        self.assertEqual(e["equity_close"], 100150.0)
        self.assertEqual(e["day_pnl"], 150.0)
        self.assertEqual(e["day_pnl_pct"], 0.15)

    def test_no_cycles(self):
        self.assertEqual(review.equity_facts([])["cycles"], 0)


class DecisionAuditTest(unittest.TestCase):
    def test_counts_and_groupings(self):
        a = review.decision_audit(RECORDS)
        self.assertEqual(a["decisions"], 3)
        self.assertEqual(a["holds"], 2)
        self.assertEqual(a["proposals"], 2)
        self.assertEqual(a["submitted"], 2)
        self.assertEqual(a["submitted_entries"], 1)
        self.assertEqual(a["submitted_exits"], 1)
        self.assertEqual(a["exit_reasons"], {"take_profit": 1})
        self.assertEqual(a["rejected"], 2)
        self.assertEqual(a["rejections_by_rule"], {"not in underlyings whitelist": 2})
        self.assertEqual(a["rejections_by_symbol"], {"TSLA": 2})
        self.assertEqual(a["errors"], 1)
        self.assertEqual(a["models"], {"m1": 3})
        self.assertEqual(a["truncated_outputs"], 1)
        self.assertEqual(a["tokens_in"], 15100)
        self.assertEqual(a["tokens_out"], 842)
        self.assertEqual(a["latency_max_sec"], 9.0)

    def test_one_verbatim_example_per_rejection_rule(self):
        """#155: the reviewer only ever saw the collapsed key ('price must
        be'), invented a fill-price mechanism, and recommended widening the
        strike band. The full detail travels beside each key now."""
        records = [
            rec("10:00:00", "order_rejected", side="buy", qty=1, symbol="SPY260904P00766000", detail="price must be positive"),
            rec("10:10:00", "order_rejected", side="buy", qty=1, symbol="SPY260904P00766000", detail="price must be positive"),
            rec("10:20:00", "order_rejected", side="buy", qty=1, symbol="TSLA", detail="TSLA not in underlyings whitelist"),
        ]

        a = review.decision_audit(records)

        self.assertEqual(a["rejections_by_rule"], {"price must be": 2, "not in underlyings whitelist": 1})
        self.assertEqual(a["rejection_examples"], {"price must be": "price must be positive",
                                                   "not in underlyings whitelist": "TSLA not in underlyings whitelist"})

    def test_rejection_keys_drop_numbers(self):
        self.assertEqual(review._rejection_key("position value 5120.00 exceeds max_position_usd 5000"), "exceeds max_position_usd")
        self.assertEqual(review._rejection_key("already at max_positions (4)"), "max_positions")


class ConfigChangesAndCostTest(unittest.TestCase):
    def test_config_changes_collapse_repeats(self):
        c = review.config_changes(RECORDS)
        self.assertEqual([x["config_hash"] for x in c], ["aaaa", "bbbb"])
        self.assertEqual(c[1]["overrides"], ["temperature"])

    def test_cost_estimate(self):
        a = review.decision_audit(RECORDS)
        self.assertAlmostEqual(review.estimate_cost_usd(a, {"m1": {"in": 1.0, "out": 10.0}}), 15100 / 1e6 + 842 / 1e6 * 10, places=4)
        self.assertIsNone(review.estimate_cost_usd(a, {"other": {"in": 1, "out": 1}}))
        self.assertIsNone(review.estimate_cost_usd(a, None))


class DigestTest(unittest.TestCase):
    def test_build_and_render(self):
        d = review.build_digest(DAY, "test", RECORDS, {"trades": 0}, [], price_table=None)
        self.assertEqual(d["equity"]["day_pnl"], 150.0)
        self.assertEqual(len(d["decisions"]), 3)
        md = review.render_markdown(d)
        self.assertIn("# EOD review - 2026-09-01", md)
        self.assertIn("rejections by rule", md)
        self.assertIn("no completed round trips", md)
        self.assertIn("`aaaa`", md)

    def test_render_with_trades_and_recommendation(self):
        summary = {
            "trades": 2, "pnl": 30.0, "win_rate_pct": 50.0, "profit_factor": 1.5, "median_hold_min": 40.0,
            "by_exit_reason": {"take_profit": {"trades": 1}, "stop_loss": {"trades": 1}, "expiry": {"trades": 0},
                               "model": {"trades": 0}, "flatten": {"trades": 0}},
            "by_underlying": {"SPY": {"pnl": 30.0, "trades": 2}},
            "by_instrument": {"call": {"pnl": 30.0, "trades": 2}},
            "by_dte_at_entry": {"3-7": {"pnl": 30.0, "trades": 2}},
        }
        d = review.build_digest(DAY, "test", RECORDS, summary, [])
        d["recommendation"] = "Tighten the take-profit."
        md = review.render_markdown(d)
        self.assertIn("2 round trips, net **+30.00**", md)
        self.assertIn("Tighten the take-profit.", md)

    def test_render_shows_the_full_rejection_detail_beside_the_rule(self):
        md = review.render_markdown(review.build_digest(DAY, "test", RECORDS, {"trades": 0}, []))

        self.assertIn('`not in underlyings whitelist` in full: "TSLA not in underlyings whitelist"', md)

    def test_render_withheld_priors_as_shadow_scores(self):
        d = review.build_digest(DAY, "test", RECORDS, {"trades": 0}, [])
        d["prior_scores"] = {
            "baseline": 0.25, "today": {"kalshi": 0.18}, "running": {"kalshi": {"mean": 0.18, "days": 1}},
            "forecasts": [],
            "withheld": [{"underlying": "QQQ", "source": "kalshi", "p": 0.3, "outcome": 0, "brier": 0.09,
                          "suppressed": "thin: volume 90.6 < 250.0"}],
            "withheld_today": {"kalshi": 0.09},
        }

        md = review.render_markdown(d)

        self.assertIn("- kalshi: today 0.18, running 0.18 over 1 day(s)", md)
        self.assertIn("- withheld kalshi: today 0.09 - shadow-graded, not in the means above "
                      "(withheld for: thin: volume 90.6 < 250.0)", md)

    def test_render_prior_scores_when_present(self):
        d = review.build_digest(DAY, "test", RECORDS, {"trades": 0}, [])
        d["prior_scores"] = {
            "baseline": 0.25,
            "today": {"kalshi": 0.18, "chain": 0.15},
            "running": {"kalshi": {"mean": 0.21, "days": 4}, "chain": {"mean": 0.19, "days": 4}},
            "forecasts": [],
        }
        md = review.render_markdown(d)
        self.assertIn("## Prior scoring (Brier, lower is better; 0.25 = coin flip)", md)
        self.assertIn("- kalshi: today 0.18, running 0.21 over 4 day(s)", md)
        self.assertIn("- chain: today 0.15, running 0.19 over 4 day(s)", md)

    def test_render_prior_scores_skip_reason(self):
        d = review.build_digest(DAY, "test", RECORDS, {"trades": 0}, [])
        d["prior_scores"] = {"skipped": "no final daily bar for 2026-09-01 yet - run after the close"}
        md = review.render_markdown(d)
        self.assertIn("no final daily bar", md)

    def test_citation_audit_rolls_up_and_renders(self):
        """#172: a per-cycle field on decision becomes one digest line, with
        the offending quotes; days before the field existed show nothing."""
        records = RECORDS + [
            rec("11:00:05", "decision", raw="[...]", count=1, model="m1", usage={}, latency_sec=1.0, finish_reason="stop",
                citations={"checked": 2, "unsupported": [{"symbol": "QQQ260903P00708000", "quoted": "68.7%",
                                                          "nearest": {"label": "1 - QQQ Kalshi P(above)", "value": 0.874}}]}),
            rec("11:10:05", "decision", raw="[]", count=0, model="m1", usage={}, latency_sec=1.0, finish_reason="stop",
                citations={"checked": 1, "unsupported": [], "misattributed": [{"symbol": "QQQ260903P00708000", "quoted": "27.3%",
                                                                              "nearest": {"label": "SPY chain P(down>1%)", "value": 0.273}}]}),
            rec("11:20:05", "decision", raw="[]", count=0, model="m1", usage={}, latency_sec=1.0, finish_reason="stop",
                citations={"skipped": "research tools ran"}),
        ]
        a = review.decision_audit(records)
        self.assertEqual((a["citations_checked"], a["citations_unsupported"], a["citations_misattributed"], a["citations_skipped_cycles"]), (3, 1, 1, 1))
        self.assertEqual(a["citation_examples"][0]["quoted"], "68.7%")
        md = review.render_markdown(review.build_digest(DAY, "test", records, {"trades": 0}, []))
        self.assertIn("**prior citations**: 3 checked, 1 unsupported, 1 misattributed, 1 cycle(s) skipped", md)
        self.assertIn('11:00 QQQ260903P00708000 quoted "68.7%" - nearest real number: 1 - QQQ Kalshi P(above) 0.874', md)
        self.assertIn('11:10 QQQ260903P00708000 quoted "27.3%" - actually: SPY chain P(down>1%) 0.273', md)

        a = review.decision_audit(RECORDS)
        self.assertIsNone(a["citations_checked"])
        self.assertNotIn("prior citations", review.render_markdown(review.build_digest(DAY, "test", RECORDS, {"trades": 0}, [])))

    def test_no_prior_scores_no_section(self):
        d = review.build_digest(DAY, "test", RECORDS, {"trades": 0}, [])
        self.assertNotIn("Prior scoring", review.render_markdown(d))


if __name__ == "__main__":
    unittest.main()

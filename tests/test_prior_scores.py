import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot import prior_scores


def _prediction_record(ts, underlying="QQQ", p=0.6, chain_p=None, suppressed=None, chain_suppressed=None):
    body = {"p_above_reference": p, "suppressed": suppressed}
    if chain_p is not None:
        body["chain"] = {"p_above_reference": chain_p, "suppressed": chain_suppressed}
    return {"ts": ts, "event": "predictions", "account": "test", underlying: body}


def _bar(day, close):
    return {"t": f"{day}T05:00:00Z", "c": close}


BARS_UP = [_bar("2026-08-28", 100.0), _bar("2026-08-31", 101.0)]
BARS_DOWN = [_bar("2026-08-28", 100.0), _bar("2026-08-31", 99.0)]


class LastForecastsTest(unittest.TestCase):
    def test_keeps_each_sources_last_forecast_of_the_day(self):
        records = [
            _prediction_record("2026-08-31T10:00:00", p=0.4),
            _prediction_record("2026-08-31T15:00:00", p=0.7),
        ]
        self.assertEqual(prior_scores.last_forecasts(records), {"QQQ": {"kalshi": 0.7}})

    def test_chain_prior_scored_as_its_own_source(self):
        records = [_prediction_record("2026-08-31T15:00:00", p=0.6, chain_p=0.55)]
        self.assertEqual(
            prior_scores.last_forecasts(records),
            {"QQQ": {"kalshi": 0.6, "chain": 0.55}},
        )

    def test_suppressed_priors_are_not_scored(self):
        records = [_prediction_record("2026-08-31T15:00:00", p=0.6, suppressed="volume 0 below min",
                                      chain_p=0.55, chain_suppressed="too flat")]
        self.assertEqual(prior_scores.last_forecasts(records), {})

    def test_other_events_and_non_dict_fields_ignored(self):
        records = [{"ts": "2026-08-31T10:00:00", "event": "cycle_start", "equity": 100000.0}]
        self.assertEqual(prior_scores.last_forecasts(records), {})


class LastWithheldTest(unittest.TestCase):
    """#155 / #111: the priors the gate suppressed, graded in the shadows."""

    def test_captures_the_last_withheld_forecast_and_its_reason(self):
        records = [
            _prediction_record("2026-08-31T10:00:00", p=0.3, suppressed="thin: volume 53.9 < 250.0"),
            _prediction_record("2026-08-31T11:00:00", p=0.35, suppressed="thin: volume 90.6 < 250.0"),
        ]
        self.assertEqual(prior_scores.last_withheld(records),
                         {"QQQ": {"kalshi": {"p": 0.35, "reason": "thin: volume 90.6 < 250.0"}}})

    def test_shown_forecasts_are_not_withheld(self):
        records = [_prediction_record("2026-08-31T15:00:00", p=0.6)]
        self.assertEqual(prior_scores.last_withheld(records), {})

    def test_chain_withheld_separately_from_kalshi(self):
        records = [_prediction_record("2026-08-31T15:00:00", p=0.6, chain_p=0.55, chain_suppressed="too flat")]
        self.assertEqual(prior_scores.last_withheld(records),
                         {"QQQ": {"chain": {"p": 0.55, "reason": "too flat"}}})
        self.assertEqual(prior_scores.last_forecasts(records), {"QQQ": {"kalshi": 0.6}})


class ResolveOutcomeTest(unittest.TestCase):
    def test_close_above_previous_close_is_one(self):
        self.assertEqual(prior_scores.resolve_outcome(BARS_UP, "2026-08-31"), 1)

    def test_close_below_previous_close_is_zero(self):
        self.assertEqual(prior_scores.resolve_outcome(BARS_DOWN, "2026-08-31"), 0)

    def test_no_bar_for_the_day_yet_is_none(self):
        self.assertIsNone(prior_scores.resolve_outcome([_bar("2026-08-28", 100.0)], "2026-08-31"))

    def test_no_prior_session_is_none(self):
        self.assertIsNone(prior_scores.resolve_outcome([_bar("2026-08-31", 101.0)], "2026-08-31"))

    def test_bars_may_arrive_newest_first(self):
        self.assertEqual(prior_scores.resolve_outcome(list(reversed(BARS_UP)), "2026-08-31"), 1)


class ScoreTest(unittest.TestCase):
    def test_brier_is_squared_error_against_the_outcome(self):
        rows = prior_scores.score({"QQQ": {"kalshi": 0.6}}, {"QQQ": 1})
        self.assertEqual(rows, [{"underlying": "QQQ", "source": "kalshi",
                                 "p": 0.6, "outcome": 1, "brier": 0.16}])

    def test_unresolved_underlying_is_skipped(self):
        self.assertEqual(prior_scores.score({"QQQ": {"kalshi": 0.6}}, {}), [])


class ScoresLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "prior_scores.jsonl"

    def test_one_line_per_day_and_account_rerun_rewrites(self):
        rows = [{"underlying": "QQQ", "source": "kalshi", "p": 0.6, "outcome": 1, "brier": 0.16}]
        with patch.object(prior_scores, "PRIOR_SCORES_LOG", self.log):
            prior_scores.append_scores_log("2026-08-31", "test", rows)
            prior_scores.append_scores_log("2026-08-31", "test", rows)
            prior_scores.append_scores_log("2026-08-31", "official", rows)
        lines = [json.loads(x) for x in self.log.read_text().splitlines()]
        self.assertEqual([(r["date"], r["account"]) for r in lines],
                         [("2026-08-31", "test"), ("2026-08-31", "official")])
        self.assertEqual(lines[0]["day_mean"], {"kalshi": 0.16})

    def test_running_means_average_day_means_per_account(self):
        with patch.object(prior_scores, "PRIOR_SCORES_LOG", self.log):
            prior_scores.append_scores_log("2026-08-28", "test", [
                {"underlying": "QQQ", "source": "kalshi", "p": 0.6, "outcome": 1, "brier": 0.16}])
            prior_scores.append_scores_log("2026-08-31", "test", [
                {"underlying": "QQQ", "source": "kalshi", "p": 0.9, "outcome": 1, "brier": 0.01}])
            prior_scores.append_scores_log("2026-08-31", "official", [
                {"underlying": "QQQ", "source": "kalshi", "p": 0.5, "outcome": 0, "brier": 0.25}])
            running = prior_scores.running_means("test")
        self.assertEqual(running, {"kalshi": {"mean": 0.085, "days": 2}})


class ScoreDayTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "prior_scores.jsonl"

    async def test_scores_the_day_and_appends_the_log(self):
        records = [_prediction_record("2026-08-31T15:00:00", p=0.6, chain_p=0.8)]

        async def bars(account, underlyings):
            return {"QQQ": BARS_UP}

        with patch.object(prior_scores, "PRIOR_SCORES_LOG", self.log), \
             patch.object(prior_scores, "fetch_daily_bars", bars):
            block = await prior_scores.score_day("2026-08-31", "test", records)

        self.assertEqual(block["baseline"], 0.25)
        self.assertEqual(block["today"], {"chain": 0.04, "kalshi": 0.16})
        self.assertEqual(block["running"]["kalshi"], {"mean": 0.16, "days": 1})
        self.assertTrue(self.log.exists())

    async def test_withheld_priors_are_shadow_graded_outside_the_means(self):
        records = [
            _prediction_record("2026-08-31T10:00:00", p=0.3, suppressed="thin: volume 90.6 < 250.0"),
            _prediction_record("2026-08-31T15:00:00", p=0.6),  # shown later in the day
            _prediction_record("2026-08-31T15:00:00", underlying="SPY", p=0.2, suppressed="too flat"),
        ]

        async def bars(account, underlyings):
            self.assertEqual(underlyings, ["QQQ", "SPY"])
            return {"QQQ": BARS_UP, "SPY": BARS_UP}

        with patch.object(prior_scores, "PRIOR_SCORES_LOG", self.log), \
             patch.object(prior_scores, "fetch_daily_bars", bars):
            block = await prior_scores.score_day("2026-08-31", "test", records)

        self.assertEqual(block["today"], {"kalshi": 0.16})  # the shown 0.6 only
        self.assertEqual(block["running"], {"kalshi": {"mean": 0.16, "days": 1}})
        self.assertEqual(
            block["withheld"],
            [{"underlying": "QQQ", "source": "kalshi", "p": 0.3, "outcome": 1, "brier": 0.49,
              "suppressed": "thin: volume 90.6 < 250.0"},
             {"underlying": "SPY", "source": "kalshi", "p": 0.2, "outcome": 1, "brier": 0.64,
              "suppressed": "too flat"}],
        )
        self.assertEqual(block["withheld_today"], {"kalshi": 0.565})
        logged = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(logged["day_mean"], {"kalshi": 0.16})
        self.assertEqual(len(logged["withheld"]), 2)
        self.assertEqual(logged["withheld_day_mean"], {"kalshi": 0.565})

    async def test_a_day_with_only_withheld_priors_is_still_graded(self):
        records = [_prediction_record("2026-08-31T15:00:00", p=0.3, suppressed="thin: volume 90.6 < 250.0")]

        async def bars(account, underlyings):
            return {"QQQ": BARS_DOWN}

        with patch.object(prior_scores, "PRIOR_SCORES_LOG", self.log), \
             patch.object(prior_scores, "fetch_daily_bars", bars):
            block = await prior_scores.score_day("2026-08-31", "test", records)

        self.assertEqual(block["today"], {})
        self.assertEqual(block["withheld_today"], {"kalshi": 0.09})
        self.assertTrue(self.log.exists())

    async def test_no_journalled_prior_is_none_and_no_log(self):
        with patch.object(prior_scores, "PRIOR_SCORES_LOG", self.log):
            self.assertIsNone(await prior_scores.score_day("2026-08-31", "test", []))
        self.assertFalse(self.log.exists())

    async def test_unresolvable_outcome_skips_without_logging(self):
        records = [_prediction_record("2026-08-31T15:00:00", p=0.6)]

        async def bars(account, underlyings):
            return {"QQQ": [_bar("2026-08-28", 100.0)]}  # no final bar for the day

        with patch.object(prior_scores, "PRIOR_SCORES_LOG", self.log), \
             patch.object(prior_scores, "fetch_daily_bars", bars):
            block = await prior_scores.score_day("2026-08-31", "test", records)

        self.assertIn("skipped", block)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()

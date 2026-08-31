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

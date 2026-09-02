"""Hourly team email with CSV attachments (issue #87).

Nothing here touches SMTP: `send()` is the only function that talks to a
socket and it is a three-line wrapper. What is worth testing is that the
message is well-formed, the CSVs contain what a teammate would pivot on, and
that a missing configuration is a quiet no-op rather than a crash in cron."""

import argparse
import csv
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import mail_report
from bot import credentials
from bot.risk import EASTERN

NOW = datetime(2026, 9, 1, 11, 0, tzinfo=EASTERN)

CYCLE = {"ts": "2026-09-01T10:00:00-04:00", "event": "cycle_start",
         "equity": 101000.0, "start_of_day_equity": 100000.0, "day_pnl": 1000.0, "positions": 2}
FILL = {"ts": "2026-09-01T10:15:00-04:00", "event": "order_submitted", "side": "buy", "qty": 2,
        "symbol": "NVDA260909C00220000", "price": 3.71, "order_id": "abc", "reason": "Bullish setup"}
REJECT = {"ts": "2026-09-01T10:20:00-04:00", "event": "order_rejected", "side": "buy", "qty": 99,
          "symbol": "SPY", "price": 1.0, "reason": "exceeds cap"}
EVENTS = [CYCLE, FILL, REJECT, {"ts": "x", "event": "decision", "count": 1}]
FLATTEN = {"ts": "2026-09-01T10:40:11-04:00", "event": "daily_loss_flatten", "halt": True,
           "attempted": ["NVDA260909C00220000", "QQQ260904P00708000"],
           "closed": [{"symbol": "NVDA260909C00220000", "status": 200, "body": None},
                      {"symbol": "QQQ260904P00708000", "status": 200, "body": None}],
           "failed": [], "state": "closed", "message": "daily-loss cutoff: all positions closed"}


def _parse(text):
    return list(csv.DictReader(io.StringIO(text)))


class CsvTest(unittest.TestCase):
    def test_trades_csv_has_a_header_and_one_row_per_order(self):
        rows = _parse(mail_report.trades_csv(EVENTS))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "NVDA260909C00220000")
        self.assertEqual(rows[0]["event"], "order_submitted")

    def test_trades_csv_keeps_the_models_reasoning(self):
        rows = _parse(mail_report.trades_csv(EVENTS))

        self.assertEqual(rows[0]["reason"], "Bullish setup")

    def test_trades_csv_excludes_non_order_events(self):
        symbols = [r["event"] for r in _parse(mail_report.trades_csv(EVENTS))]

        self.assertNotIn("decision", symbols)
        self.assertNotIn("cycle_start", symbols)

    def test_trades_csv_has_a_sell_row_for_every_flatten_close(self):
        """#221: test on 2026-09-01 bought two NVDA contracts and the 2%
        cutoff closed four positions at 10:40; the CSV showed the buys and
        no closes."""
        rows = _parse(mail_report.trades_csv([CYCLE, FILL, FLATTEN]))

        self.assertEqual([(r["event"], r["side"], r["symbol"]) for r in rows], [
            ("order_submitted", "buy", "NVDA260909C00220000"),
            ("daily_loss_flatten", "sell", "NVDA260909C00220000"),
            ("daily_loss_flatten", "sell", "QQQ260904P00708000"),
        ])
        self.assertEqual(rows[1]["qty"], "2")
        self.assertEqual(rows[1]["exit"], "True")
        self.assertEqual(rows[1]["reason"], "daily-loss cutoff: all positions closed")

    def test_rejected_csv_carries_the_rule_and_is_empty_when_nothing_was_refused(self):
        rows = _parse(mail_report.rejected_csv([CYCLE, FILL, dict(REJECT, detail="exceeds max_position_usd")]))

        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["symbol"], rows[0]["detail"], rows[0]["reason"]), ("SPY", "exceeds max_position_usd", "exceeds cap"))
        self.assertEqual(mail_report.rejected_csv([CYCLE, FILL]), "")

    def test_cycles_csv_is_the_intraday_equity_curve(self):
        rows = _parse(mail_report.cycles_csv(EVENTS))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["equity"], "101000.0")
        self.assertEqual(rows[0]["positions"], "2")

    def test_csvs_are_still_valid_when_there_is_nothing_to_report(self):
        for text in (mail_report.trades_csv([]), mail_report.cycles_csv([])):
            rows = _parse(text)

            self.assertEqual(rows, [])
            self.assertTrue(text.strip(), "an empty CSV should still carry its header")

    def test_a_reason_containing_a_comma_survives_the_round_trip(self):
        events = [dict(FILL, reason="Bought calls, sized to the cap, per the thesis")]

        rows = _parse(mail_report.trades_csv(events))

        self.assertEqual(rows[0]["reason"], "Bought calls, sized to the cap, per the thesis")

    def test_csv_timestamps_convert_to_the_callers_clock_keeping_the_offset(self):
        """The body reads Central; the attachments should too. The rewrite is
        lossless - the ISO offset travels with the value - so a pivot in any
        tool still parses the true instant."""
        rows = _parse(mail_report.trades_csv(EVENTS, tz=ZoneInfo("America/Chicago")))

        self.assertEqual(rows[0]["ts"], "2026-09-01T09:15:00-05:00")

    def test_csv_timestamps_pass_through_untouched_without_a_tz(self):
        rows = _parse(mail_report.trades_csv(EVENTS))

        self.assertEqual(rows[0]["ts"], "2026-09-01T10:15:00-04:00")

    def test_a_malformed_timestamp_survives_the_conversion(self):
        rows = _parse(mail_report.trades_csv([dict(FILL, ts="not-a-date")], tz=ZoneInfo("America/Chicago")))

        self.assertEqual(rows[0]["ts"], "not-a-date")

    def test_equity_csv_reads_the_multiday_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "equity.jsonl"
            path.write_text('{"day": "2026-09-01", "account": "official", "equity_close": 101000}\n')

            rows = _parse(mail_report.equity_csv(path))

        self.assertEqual(rows[0]["day"], "2026-09-01")

    def test_equity_csv_is_empty_when_the_log_is_missing(self):
        self.assertEqual(mail_report.equity_csv(Path("/nonexistent/equity.jsonl")), "")

    def test_equity_csv_skips_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "equity.jsonl"
            path.write_text('{"day": "2026-09-01"}\nnot json\n{"day": "2026-09-02"}\n')

            rows = _parse(mail_report.equity_csv(path))

        self.assertEqual([r["day"] for r in rows], ["2026-09-01", "2026-09-02"])


class SummaryTest(unittest.TestCase):
    def test_counts_the_day(self):
        s = mail_report.summarize(EVENTS, "official")

        self.assertEqual((s["equity"], s["day_pnl"], s["positions"]), (101000.0, 1000.0, 2))
        self.assertEqual((s["filled"], s["rejected"]), (1, 1))
        self.assertEqual(s["cycles"], 1)

    def test_derives_day_pnl_when_the_journal_omits_it(self):
        cycle = {k: v for k, v in CYCLE.items() if k != "day_pnl"}

        s = mail_report.summarize([cycle], "official")

        self.assertEqual(s["day_pnl"], 1000.0)

    def test_halt_comes_from_the_current_state_not_the_journal(self):
        """A manual_halt event stays in today's journal after the halt is
        cleared. Deriving the flag from events marked the account halted for
        the rest of the day - caught live on CT 108, and the same trap
        RiskManager.halt_state() already documents (#74)."""
        events = [CYCLE, {"ts": "x", "event": "manual_halt"}]

        s = mail_report.summarize(events, "official", halt="none")

        self.assertFalse(s["halted"], "a cleared halt must not keep flagging the report")

    def test_reports_a_live_halt_and_names_the_kind(self):
        s = mail_report.summarize([CYCLE], "official", halt="daily_loss")

        self.assertTrue(s["halted"])
        self.assertEqual(s["halt"], "daily_loss")
        self.assertIn("daily_loss", mail_report.subject_line(s, NOW))

    def test_empty_journal_does_not_raise(self):
        s = mail_report.summarize([], "test")

        self.assertIsNone(s["equity"])
        self.assertEqual(s["cycles"], 0)


SETTINGS = {"REPORT_EMAIL_TO": "a@example.com, b@example.com", "REPORT_EMAIL_FROM": "bot@example.com"}


class MessageTest(unittest.TestCase):
    def _message(self, events=EVENTS, settings=None):
        summary = mail_report.summarize(events, "official")
        attachments = {
            "trades.csv": mail_report.trades_csv(events),
            "cycles.csv": mail_report.cycles_csv(events),
            "empty.csv": "",
        }
        return mail_report.build_message(summary, attachments, settings or SETTINGS, NOW)

    def test_subject_carries_the_headline_numbers(self):
        subject = self._message()["Subject"]

        self.assertIn("official", subject)
        self.assertIn("$101,000.00", subject)
        self.assertIn("+1,000.00", subject)

    def test_subject_flags_a_halt_so_it_is_visible_without_opening(self):
        summary = mail_report.summarize([CYCLE], "official", halt="manual")

        self.assertIn("[HALTED", mail_report.subject_line(summary, NOW))

    def test_recipients_are_split_on_commas_and_semicolons(self):
        self.assertEqual(
            mail_report.recipients({"REPORT_EMAIL_TO": "a@x.com; b@x.com ,c@x.com"}),
            ["a@x.com", "b@x.com", "c@x.com"],
        )

    def test_no_recipients_configured_is_an_empty_list_not_a_crash(self):
        self.assertEqual(mail_report.recipients({}), [])

    def test_attachments_are_csv_and_empty_ones_are_skipped(self):
        msg = self._message()
        names = [p.get_filename() for p in msg.iter_attachments()]

        self.assertEqual(names, ["trades.csv", "cycles.csv"])
        self.assertTrue(all(p.get_content_type() == "text/csv" for p in msg.iter_attachments()))

    def test_attachment_content_survives_intact(self):
        msg = self._message()
        part = next(p for p in msg.iter_attachments() if p.get_filename() == "trades.csv")

        self.assertIn("NVDA260909C00220000", part.get_content())

    def test_body_names_the_account_and_says_it_is_read_only(self):
        body = self._message().get_body(preferencelist=("plain",)).get_content()

        self.assertIn("official", body)
        self.assertIn("read-only", body)

    def test_marked_auto_generated_so_it_does_not_trip_vacation_responders(self):
        self.assertEqual(self._message()["Auto-Submitted"], "auto-generated")

    def test_body_carries_the_full_reason_never_an_ellipsis(self):
        """The 400-char clip is a Home Assistant constraint; in the email it
        cut journal entries mid-sentence (the CSVs had the full text, but
        nobody opens a CSV to finish a sentence)."""
        reason = "because " * 100  # ~800 chars, past every historical cap
        body = self._message([dict(FILL, reason=reason.strip())]).get_body(
            preferencelist=("plain",)).get_content()

        self.assertIn(reason.strip(), body)
        self.assertNotIn("…", body)

    def test_body_lists_every_trade_today_not_the_last_twelve(self):
        events = [dict(FILL, symbol=f"SYM{i}") for i in range(15)]
        body = self._message(events).get_body(preferencelist=("plain",)).get_content()

        for i in range(15):
            self.assertIn(f"SYM{i}", body)

    def test_rejections_are_counted_in_the_body_but_not_listed(self):
        """#229: rejected orders buried the fills under orders that never
        happened. They keep their count and move to their own CSV."""
        body = self._message().get_body(preferencelist=("plain",)).get_content()

        self.assertIn("1 filled, 0 flattened, 1 rejected (see the rejected CSV)", body)
        self.assertIn("NVDA260909C00220000", body)
        self.assertNotIn("rejected buy", body)
        self.assertNotIn("exceeds cap", body)

    def test_flatten_closes_are_listed_in_the_body(self):
        body = self._message([CYCLE, FILL, FLATTEN]).get_body(preferencelist=("plain",)).get_content()

        self.assertIn("FLATTENED (daily loss)", body)
        self.assertIn("QQQ260904P00708000", body)

    def test_trade_times_render_on_the_callers_clock(self):
        """Journal timestamps are Eastern; the report passes the host's tz
        (Central on CT 108) so the email agrees with the cron logs instead
        of citing EDT."""
        s = mail_report.summarize([FILL], "official", tz=ZoneInfo("America/Chicago"))

        self.assertEqual(s["trades"][0]["time"], "09:15")  # 10:15-04:00


class SettingsResolutionTest(unittest.TestCase):
    """Cron inherits almost no environment - the same trap that silently
    disabled MQTT under cron (#76). Settings must resolve from the deployed
    credentials file, not just os.environ."""

    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.prod = Path(tmpdir.name)
        patch = mock.patch.object(credentials, "PRODUCTION_CREDENTIALS_DIR", self.prod)
        patch.start()
        self.addCleanup(patch.stop)

    def test_resolves_from_the_credentials_file_with_an_empty_environment(self):
        (self.prod / "credentials.env").write_text("REPORT_EMAIL_TO=team@example.com\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = mail_report.load_report_env("official")

        self.assertEqual(settings["REPORT_EMAIL_TO"], "team@example.com")

    def test_environment_wins_over_the_file(self):
        (self.prod / "credentials.env").write_text("REPORT_EMAIL_TO=file@example.com\n")

        with mock.patch.dict(os.environ, {"REPORT_EMAIL_TO": "env@example.com"}, clear=True):
            settings = mail_report.load_report_env("official")

        self.assertEqual(settings["REPORT_EMAIL_TO"], "env@example.com")

    def test_each_account_reads_its_own_file(self):
        (self.prod / "credentials.env").write_text("REPORT_EMAIL_TO=official@example.com\n")
        (self.prod / "credentials-test.env").write_text("REPORT_EMAIL_TO=test@example.com\n")

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mail_report.load_report_env("test")["REPORT_EMAIL_TO"], "test@example.com")

    def test_missing_file_is_not_an_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mail_report.load_report_env("official"), {})


class SuppressReasonTest(unittest.TestCase):
    """Idle-hour suppression (issue #153): the decision is a pure function of
    (events, now, halt, force, window), so it is tested with plain datetimes -
    no mocking the clock out of the module."""

    NOW = datetime(2026, 9, 1, 11, 0, tzinfo=EASTERN)

    def _trade(self, ts):
        return {"ts": ts, "event": "order_submitted", "symbol": "SPY"}

    def test_sends_when_a_trade_landed_inside_the_window(self):
        events = [self._trade("2026-09-01T10:15:00-04:00")]

        self.assertIsNone(mail_report.suppress_reason(events, self.NOW, "none"))

    def test_suppresses_when_the_only_trade_is_older_than_the_window(self):
        events = [self._trade("2026-09-01T09:30:00-04:00")]

        reason = mail_report.suppress_reason(events, self.NOW, "none")

        self.assertIn("no trading activity", reason)

    def test_cycles_and_other_chatter_are_not_activity(self):
        events = [{"ts": "2026-09-01T10:50:00-04:00", "event": "cycle_start"},
                  {"ts": "2026-09-01T10:55:00-04:00", "event": "decision"}]

        self.assertIsNotNone(mail_report.suppress_reason(events, self.NOW, "none"))

    def test_a_halt_always_sends_even_with_no_activity(self):
        for halt in ("daily_loss", "manual", "unknown"):
            self.assertIsNone(mail_report.suppress_reason([], self.NOW, halt), halt)

    def test_force_always_sends(self):
        self.assertIsNone(mail_report.suppress_reason([], self.NOW, "none", force=True))

    def test_a_flatten_that_closed_positions_counts_as_activity(self):
        """flatten's closes never journal as orders (bot/flatten.py writes no
        journal events) - without this the hour the bot closed everything
        would read as a quiet hour and the report would be suppressed."""
        events = [{"ts": "2026-09-01T10:50:00-04:00", "event": "flatten", "closed": ["SPY260904C00770000"]}]

        self.assertIsNone(mail_report.suppress_reason(events, self.NOW, "none"))

    def test_the_daily_empty_flatten_backstop_is_not_activity(self):
        events = [{"ts": "2026-09-01T10:50:00-04:00", "event": "flatten",
                   "attempted": [], "closed": [], "failed": []}]

        self.assertIsNotNone(mail_report.suppress_reason(events, self.NOW, "none"))

    def test_malformed_or_missing_timestamps_are_skipped_not_trusted(self):
        events = [self._trade("not-a-date"), {"event": "order_submitted"}]

        self.assertIsNotNone(mail_report.suppress_reason(events, self.NOW, "none"))

    def test_a_wider_window_reaches_an_older_trade(self):
        events = [self._trade("2026-09-01T09:30:00-04:00")]

        self.assertIsNone(mail_report.suppress_reason(events, self.NOW, "none", window_minutes=120))


class SuppressionRunTest(unittest.TestCase):
    """One thin integration test per outcome: run() respects suppress_reason.
    The clock is real - a quiet hour is simulated with an empty journal, an
    active one with an event stamped moments ago."""

    def _args(self, **over):
        base = {"account": "test", "config": None, "dry_run": False, "force": False, "window_minutes": 60}
        base.update(over)
        return argparse.Namespace(**base)

    def _run(self, events, halt="none", args=None):
        rm = mock.Mock()
        rm.halt_state.return_value = halt
        with mock.patch.object(mail_report, "send") as send, \
                mock.patch.object(mail_report, "RiskManager", return_value=rm), \
                mock.patch.object(mail_report, "load_config"), \
                mock.patch.object(mail_report.journal, "read_events", return_value=events), \
                mock.patch.object(mail_report.journal, "use_account"), \
                mock.patch.object(mail_report, "load_report_env",
                                  return_value={"REPORT_EMAIL_TO": "team@example.com"}), \
                mock.patch.object(mail_report, "equity_csv", return_value=""):
            ret = mail_report.run(args or self._args())
        return ret, send

    def test_a_quiet_hour_sends_nothing(self):
        ret, send = self._run([])

        self.assertEqual(ret, 0)
        send.assert_not_called()

    def test_a_recent_trade_sends(self):
        events = [{"ts": datetime.now(EASTERN).isoformat(timespec="seconds"),
                   "event": "order_submitted", "side": "buy", "qty": 1, "symbol": "SPY", "price": 1.0}]

        ret, send = self._run(events)

        self.assertEqual(ret, 0)
        send.assert_called_once()

    def test_a_halt_sends_despite_the_quiet_hour(self):
        _, send = self._run([], halt="daily_loss")

        send.assert_called_once()

    def test_force_sends_despite_the_quiet_hour(self):
        _, send = self._run([], args=self._args(force=True))

        send.assert_called_once()

    def test_dry_run_marks_the_suppression(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            _, send = self._run([], args=self._args(dry_run=True))

        self.assertIn("[suppressed]", out.getvalue())
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()

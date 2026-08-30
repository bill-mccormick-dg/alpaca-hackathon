"""Hourly team email with CSV attachments (issue #87).

Nothing here touches SMTP: `send()` is the only function that talks to a
socket and it is a three-line wrapper. What is worth testing is that the
message is well-formed, the CSVs contain what a teammate would pivot on, and
that a missing configuration is a quiet no-op rather than a crash in cron."""

import csv
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()

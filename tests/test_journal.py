import json
import tempfile
import unittest
from pathlib import Path

from bot import journal


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / "logs" / "journal.jsonl"

    def _write(self, ts, event, **fields):
        self.journal.parent.mkdir(exist_ok=True)
        with self.journal.open("a") as f:
            f.write(json.dumps({"ts": ts, "event": event, **fields}) + "\n")

    def test_log_creates_dir_and_appends_json_line(self):
        journal.log("cycle_start", journal=self.journal, equity=100.0)
        journal.log("cycle_end", journal=self.journal, actions=0)

        lines = self.journal.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["event"], "cycle_start")
        self.assertEqual(first["equity"], 100.0)
        self.assertIn("ts", first)

    def test_log_serializes_non_json_values_as_strings(self):
        journal.log("x", journal=self.journal, path=Path("/tmp/a"))
        self.assertEqual(json.loads(self.journal.read_text())["path"], "/tmp/a")

    def test_read_events_missing_journal_is_empty(self):
        self.assertEqual(journal.read_events("2026-01-15", journal=self.journal), [])

    def test_read_events_filters_by_day_and_event(self):
        self._write("2026-01-15T10:00:00-05:00", "cycle_start")
        self._write("2026-01-15T10:01:00-05:00", "order_submitted", symbol="AAPL")
        self._write("2026-01-16T10:00:00-05:00", "cycle_start")

        self.assertEqual(len(journal.read_events("2026-01-15", journal=self.journal)), 2)
        self.assertEqual(len(journal.read_events("all", journal=self.journal)), 3)
        only = journal.read_events("2026-01-15", events=("order_submitted",), journal=self.journal)
        self.assertEqual([r["symbol"] for r in only], ["AAPL"])

    def test_read_events_skips_malformed_lines(self):
        self._write("2026-01-15T10:00:00-05:00", "cycle_start")
        with self.journal.open("a") as f:
            f.write('{"ts": "2026-01-15T10:01:00-05:00", "event": "cycle_e\n')  # truncated by a crash
        self._write("2026-01-15T10:02:00-05:00", "cycle_end")

        self.assertEqual(len(journal.read_events("2026-01-15", journal=self.journal)), 2)

    def test_daily_summary_counts_and_collects_trades(self):
        day = "2026-01-15"
        self._write(f"{day}T10:00:00-05:00", "cycle_start", equity=100000.0, day_pnl=0.0)
        self._write(f"{day}T10:00:05-05:00", "order_rejected", symbol="TSLA", reason="not whitelisted")
        self._write(
            f"{day}T10:00:06-05:00", "order_submitted",
            side="buy", qty=2, symbol="AAPL260204C00200000", instrument="option", reason="cheap gamma",
        )
        self._write(f"{day}T10:15:00-05:00", "cycle_start", equity=100250.0, day_pnl=250.0)
        self._write(f"{day}T10:15:01-05:00", "error", where="decide", detail="timeout")
        self._write(f"{day}T15:00:00-05:00", "daily_loss_halt", equity=97000.0)

        s = journal.daily_summary(day, journal=self.journal)

        self.assertEqual(s["cycles"], 2)
        self.assertEqual(s["orders"], 1)
        self.assertEqual(s["rejected"], 1)
        self.assertEqual(s["errors"], 1)
        self.assertEqual(s["halts"], ["daily_loss_halt"])
        self.assertEqual(s["equity"], 97000.0)  # last seen
        self.assertEqual(s["day_pnl"], 250.0)
        self.assertEqual(s["trades"][0]["symbol"], "AAPL260204C00200000")
        self.assertEqual(s["trades"][0]["side"], "buy")

    def test_daily_summary_empty_day(self):
        s = journal.daily_summary("2026-01-15", journal=self.journal)
        self.assertEqual(s["cycles"], 0)
        self.assertEqual(s["trades"], [])
        self.assertIsNone(s["equity"])


if __name__ == "__main__":
    unittest.main()

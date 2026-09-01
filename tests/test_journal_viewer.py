"""The web viewer's moving parts, without a browser or a running server.

The tail logic and the merge/replay endpoints are pure enough to test
directly; the HTTP layer is stdlib and gets one socket-level smoke test.
"""

import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import journal_viewer


def write_lines(path: Path, *records):
    with open(path, "a") as f:
        f.writelines(json.dumps(r) + "\n" for r in records)


class AccountNamingTest(unittest.TestCase):
    def test_official_is_the_unsuffixed_file(self):
        """bot/journal.py gives the official account the default filename;
        the viewer must agree or officials's stream would be labelled
        'journal'."""
        self.assertEqual(journal_viewer.account_for(Path("/x/journal.jsonl")), "official")

    def test_suffixed_files_name_their_account(self):
        self.assertEqual(journal_viewer.account_for(Path("/x/journal-test.jsonl")), "test")
        self.assertEqual(journal_viewer.account_for(Path("/x/journal-mixed.jsonl")), "mixed")


class TailerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_first_sighting_starts_at_the_end_not_the_beginning(self):
        """History belongs to the backlog endpoint; the stream is 'from
        now'. Replaying the whole file to every new subscriber would
        duplicate what backlog() already sent."""
        write_lines(self.dir / "journal.jsonl", {"ts": "t1", "event": "old"})
        tailer = journal_viewer.Tailer(self.dir)
        self.assertEqual(tailer.poll_once(), [])
        write_lines(self.dir / "journal.jsonl", {"ts": "t2", "event": "new"})
        out = tailer.poll_once()
        self.assertEqual([r["event"] for r in out], ["new"])
        self.assertEqual(out[0]["_account"], "official")

    def test_a_file_created_after_startup_is_picked_up(self):
        tailer = journal_viewer.Tailer(self.dir)
        tailer.poll_once()
        write_lines(self.dir / "journal-test.jsonl", {"ts": "t", "event": "e"})
        tailer.poll_once()  # first sighting primes the offset...
        write_lines(self.dir / "journal-test.jsonl", {"ts": "t2", "event": "e2"})
        out = tailer.poll_once()  # ...so only genuinely new lines stream
        self.assertEqual([r["event"] for r in out], ["e2"])

    def test_truncation_restarts_from_zero_instead_of_erroring(self):
        f = self.dir / "journal.jsonl"
        write_lines(f, {"ts": "t", "event": "a"}, {"ts": "t", "event": "b"})
        tailer = journal_viewer.Tailer(self.dir)
        tailer.poll_once()
        f.write_text(json.dumps({"ts": "t", "event": "fresh"}) + "\n")
        self.assertEqual([r["event"] for r in tailer.poll_once()], ["fresh"])

    def test_garbage_lines_are_skipped(self):
        f = self.dir / "journal.jsonl"
        f.touch()  # exists before the first poll, so the offset is primed
        tailer = journal_viewer.Tailer(self.dir)
        tailer.poll_once()
        with open(f, "a") as fh:
            fh.write("not json\n")
        write_lines(f, {"ts": "t", "event": "good"})
        self.assertEqual([r["event"] for r in tailer.poll_once()], ["good"])

    def test_a_slow_subscriber_loses_events_rather_than_blocking_the_tail(self):
        tailer = journal_viewer.Tailer(self.dir)
        q = tailer.subscribe()
        while not q.full():
            q.put_nowait({})
        tailer.poll_once()
        write_lines(self.dir / "journal.jsonl", {"ts": "t", "event": "e"})
        tailer.poll_once()  # must not raise


class BacklogTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_merges_accounts_in_time_order(self):
        """tail -F replays per file; the whole point of the merge is that
        one account's morning cannot render after another's afternoon."""
        write_lines(self.dir / "journal.jsonl", {"ts": "2026-08-31T10:00:00-04:00", "event": "b"})
        write_lines(self.dir / "journal-test.jsonl",
                    {"ts": "2026-08-31T09:00:00-04:00", "event": "a"},
                    {"ts": "2026-08-31T11:00:00-04:00", "event": "c"})
        out = journal_viewer.backlog(self.dir)
        self.assertEqual([r["event"] for r in out], ["a", "b", "c"])
        self.assertEqual([r["_account"] for r in out], ["test", "official", "test"])


class HttpSmokeTest(unittest.TestCase):
    """One real server on an ephemeral port: the page serves, history
    validates its input, and unknown paths 404. The SSE stream is exercised
    by every live deployment within seconds, and testing it here would mean
    threads reading a chunked socket - the payoff is not worth the flake."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        write_lines(cls.dir / "journal.jsonl",
                    {"ts": "2026-08-28T10:00:00-04:00", "event": "decision", "count": 1})
        cls.server = journal_viewer.serve(0, cls.dir)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5)

    def test_the_page_bounds_its_own_growth_and_follows_the_live_end(self):
        """A day is ~1200 events across three accounts; an unbounded feed
        makes the tab crawl by the close. And a reader at the live end must
        be carried along, while a reader scrolled up must not be yanked."""
        html = self._get("/").read().decode()
        self.assertIn("MAX_ROWS", html)
        self.assertIn("function trim()", html)
        self.assertIn("function pinned()", html)
        self.assertIn("older events trimmed from view", html)

    def test_page_serves_and_is_self_contained(self):
        html = self._get("/").read().decode()
        self.assertIn("journal", html)
        # Self-contained like the slide deck: no external resource can make
        # the page depend on a CDN from inside a tunnel.
        self.assertNotIn("http://", html.split("<body>")[-1])
        self.assertNotIn("https://", html)

    def test_history_returns_that_days_records(self):
        records = json.loads(self._get("/history?day=2026-08-28").read())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["_account"], "official")

    def test_history_rejects_a_malformed_day(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/history?day=notaday")
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_path_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/nope")
        self.assertEqual(ctx.exception.code, 404)


class ReasonsAreNotTruncatedTest(unittest.TestCase):
    """The renderer is JavaScript inside a Python string, so these assert on
    the page source rather than on behaviour. Issue #174: reasons were cut at
    220 characters mid-word, and the model's reasoning is the whole point of
    the page - the row CSS already wraps, and MAX_ROWS caps the page by row
    count, so nothing needed the character limit."""

    def test_no_character_slice_is_applied_to_a_reason(self):
        for line in journal_viewer.PAGE.splitlines():
            if "reason" in line and ".slice(" in line:
                self.fail(f"a reason is being truncated: {line.strip()}")

    def test_rejections_show_the_models_case_as_well_as_the_verdict(self):
        """A rejection needs both facts - bot/report.py::_trade_line makes the
        same argument for the email. (Anchored on the renderer branch, not the
        first mention: the CSS names the event class too.)"""
        lines = journal_viewer.PAGE.splitlines()
        start = next(i for i, x in enumerate(lines) if "e === 'order_rejected'" in x)
        branch = "\n".join(lines[start:start + 4])

        self.assertIn("r.detail", branch, "the funnel's verdict must still show")
        self.assertIn("r.reason", branch, "the model's case must show too")

    def test_the_page_still_caps_rows_rather_than_characters(self):
        self.assertIn("MAX_ROWS", journal_viewer.PAGE)
        self.assertIn("white-space:pre-wrap", journal_viewer.PAGE)


class PageJavaScriptParsesTest(unittest.TestCase):
    """The renderer is JavaScript inside a Python string, so Python's own
    syntax check never sees it and a stray brace ships a blank page to
    everyone - including judges - with every server-side test still green.
    Skipped when node is unavailable; CI's ubuntu-latest has it."""

    def test_the_pages_script_is_syntactically_valid(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        match = re.search(r"<script>(.*?)</script>", journal_viewer.PAGE, re.DOTALL)
        self.assertIsNotNone(match, "the page should carry exactly one inline script")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed.js"
            path.write_text(match.group(1))
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, f"page JavaScript does not parse:\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()

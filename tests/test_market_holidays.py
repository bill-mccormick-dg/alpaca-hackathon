"""The deploy freeze reads .github/market-holidays.txt (see deploy.yml).

The gate treats an unlisted date as a trading day, so a malformed or stale
list cannot open the freeze - it can only leave it shut. That makes the
failure mode quiet: nobody notices an over-blocking gate until they are
waiting on a merge at 09:00 on a holiday. These checks make it loud instead.
"""

import re
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

HOLIDAYS = Path(__file__).resolve().parent.parent / ".github" / "market-holidays.txt"
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")

# Weekday closures per year on the NYSE calendar. Eight is the floor: a year
# where two of the ten land on a weekend still leaves this many, and a year
# listing fewer than this has almost certainly been half-entered.
MIN_PER_YEAR = 8


def parse() -> list[date]:
    """Same strip-comments-then-match the gate's `sed`/`grep` pair does."""
    out = []
    for line in HOLIDAYS.read_text().splitlines():
        token = line.split("#", 1)[0].strip()
        if not token:
            continue
        m = DATE_RE.match(token)
        assert m, f"unparsable line (the gate would silently drop it): {line!r}"
        out.append(date.fromisoformat(m.group(1)))
    return out


class MarketHolidaysFile(unittest.TestCase):
    def setUp(self):
        self.dates = parse()

    def test_file_is_not_empty(self):
        self.assertTrue(self.dates, "no dates parsed - the gate would freeze every weekday")

    def test_sorted_and_unique(self):
        self.assertEqual(self.dates, sorted(self.dates), "dates are not in ascending order")
        self.assertEqual(len(self.dates), len(set(self.dates)), "duplicate dates")

    def test_no_weekends(self):
        """The gate skips Saturday and Sunday before it ever looks here, so a
        weekend entry is dead weight and a sign the year was entered by hand
        without checking the weekday."""
        weekend = [d for d in self.dates if d.weekday() >= 5]
        self.assertEqual(weekend, [], f"weekend dates listed: {weekend}")

    def test_each_year_looks_complete(self):
        years = {}
        for d in self.dates:
            years.setdefault(d.year, []).append(d)
        for year, days in sorted(years.items()):
            self.assertGreaterEqual(
                len(days), MIN_PER_YEAR,
                f"{year} lists only {len(days)} holidays - looks half-entered",
            )

    def test_covers_the_current_year(self):
        """Fails the year after the list runs out, which is the reminder to
        extend it. The gate warns at the same point; this fails the build so
        the warning is not the only signal."""
        last = max(d.year for d in self.dates)
        this_year = datetime.now(tz=timezone.utc).year
        self.assertGreaterEqual(
            last, this_year, f"holiday list ends in {last}; extend it past {this_year}",
        )

    def test_rule_7_2_exception_is_not_listed(self):
        """NYSE Rule 7.2 moves a Saturday holiday to the preceding Friday
        UNLESS that Friday is the last trading day of the year. 2027-12-31 is
        that case: the exchange is open, so listing it would wrongly open the
        freeze on a live session."""
        self.assertNotIn(date(2027, 12, 31), self.dates)


if __name__ == "__main__":
    unittest.main()

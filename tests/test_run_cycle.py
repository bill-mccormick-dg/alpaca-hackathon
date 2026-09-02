import unittest
from datetime import datetime
from pathlib import Path

import run_cycle
from bot.risk import EASTERN


class OfficialAccountGateTest(unittest.TestCase):
    def test_refuses_before_official_window_opens(self):
        # Sunday night before the Monday 9:30 ET start.
        now = datetime(2026, 8, 30, 21, 0, tzinfo=EASTERN)
        self.assertFalse(run_cycle.official_account_may_trade(now))

    def test_refuses_one_minute_before_open(self):
        now = datetime(2026, 8, 31, 9, 29, tzinfo=EASTERN)
        self.assertFalse(run_cycle.official_account_may_trade(now))

    def test_allows_at_the_exact_open(self):
        now = datetime(2026, 8, 31, 9, 30, tzinfo=EASTERN)
        self.assertTrue(run_cycle.official_account_may_trade(now))

    def test_allows_after_open(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=EASTERN)
        self.assertTrue(run_cycle.official_account_may_trade(now))


class DescribeErrorTest(unittest.TestCase):
    def test_includes_type_and_message(self):
        self.assertEqual(run_cycle.describe_error(ValueError("bad json")), "ValueError: bad json")

    def test_empty_message_still_names_the_type(self):
        # httpx timeouts stringify to "" - the first live Featherless
        # timeout journaled as detail="" before this existed.
        class ReadTimeout(Exception):
            pass

        self.assertEqual(run_cycle.describe_error(ReadTimeout()), "ReadTimeout")


class AccountFromSnapshotTest(unittest.TestCase):
    def test_round_trips_serialized_account(self):
        snap = {
            "account": {
                "equity": 101000.0,
                "start_of_day_equity": 100000.0,
                "cash": 95000.0,
                "positions": [
                    {
                        "symbol": "AAPL260204C00200000",
                        "instrument": "option",
                        "qty": 2.0,
                        "market_value": 1100.0,
                        "underlying": "AAPL",
                    },
                    {"symbol": "SPY", "instrument": "stock", "qty": 5.0, "market_value": 3800.0, "underlying": None},
                ],
            }
        }
        acct = run_cycle.account_from_snapshot(snap)

        self.assertEqual(acct.equity, 101000.0)
        self.assertEqual(acct.start_of_day_equity, 100000.0)
        self.assertEqual(acct.cash, 95000.0)
        self.assertEqual(acct.open_position_count, 2)
        self.assertEqual(acct.positions["AAPL260204C00200000"].underlying, "AAPL")
        self.assertEqual(acct.positions["SPY"].qty, 5.0)

    def test_empty_positions(self):
        snap = {"account": {"equity": 1.0, "start_of_day_equity": 1.0, "cash": 1.0, "positions": []}}
        self.assertEqual(run_cycle.account_from_snapshot(snap).positions, {})

    def test_open_orders_round_trip_and_unknown_stays_unknown(self):
        """#171: [] is 'checked, none resting', None is 'lookup failed'."""
        base = {"equity": 1.0, "start_of_day_equity": 1.0, "cash": 1.0, "positions": []}
        order = {"id": "a", "client_order_id": "hb-x", "symbol": "QQQ260903P00709000", "side": "buy", "qty": 4.0,
                 "filled_qty": 0.0, "order_type": "limit", "limit_price": 3.56, "submitted_at": "2026-09-01T16:00:17Z",
                 "instrument": "option"}
        acct = run_cycle.account_from_snapshot({"account": dict(base, open_orders=[order])})
        self.assertEqual([o.symbol for o in acct.pending_buys()], ["QQQ260903P00709000"])
        self.assertEqual(acct.committed_position_count, 1)
        self.assertEqual(run_cycle.account_from_snapshot({"account": dict(base, open_orders=[])}).open_orders, [])
        self.assertIsNone(run_cycle.account_from_snapshot({"account": dict(base, open_orders=None)}).open_orders)
        self.assertIsNone(run_cycle.account_from_snapshot({"account": base}).open_orders)




class CycleEndAlwaysPublishesTest(unittest.TestCase):
    """Every cycle-end path must go through run_cycle's end_cycle() helper.

    There are four of them - exits-only, final-day skip, no-proposals, and the
    normal finish - and an earlier version hooked the trade-report publish to
    only the first. So the Home Assistant trade and report cards
    refreshed only on cycles that closed a position, which on a normal day is
    never. Nothing failed; the card just sat at `unknown`.

    A static check because the alternative is driving four full cycles."""

    SOURCE = Path(__file__).resolve().parent.parent / "run_cycle.py"

    def test_no_cycle_end_bypasses_the_helper(self):
        lines = self.SOURCE.read_text().splitlines()
        offenders = [
            (n, line.strip())
            for n, line in enumerate(lines, 1)
            if 'journal.log("cycle_end"' in line and "def end_cycle" not in line
        ]
        # Exactly one is allowed: the call inside end_cycle() itself.
        self.assertEqual(
            len(offenders), 1,
            f"cycle_end must be journaled only via end_cycle(); found {offenders}",
        )

    def test_the_helper_publishes_the_trade_report(self):
        source = self.SOURCE.read_text()
        helper = source.split("def end_cycle", 1)[1].split("journal.log(\"cycle_end\"", 1)[0]

        self.assertIn("publish_report", helper)
        self.assertIn("recent_trades", helper)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime

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


if __name__ == "__main__":
    unittest.main()

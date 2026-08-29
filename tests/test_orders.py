import unittest

from bot import orders


class ClassifyCloseResultsTest(unittest.TestCase):
    def test_2xx_entries_are_closed_others_failed(self):
        results = [
            {"symbol": "AAPL", "status": 200, "body": {"id": "1"}},
            {"symbol": "SPY", "status": 422, "body": {"message": "insufficient qty"}},
            {"symbol": "QQQ", "status": "207", "body": None},
        ]
        closed, failed = orders.classify_close_results(results)
        self.assertEqual([c["symbol"] for c in closed], ["AAPL", "QQQ"])
        self.assertEqual([f["symbol"] for f in failed], ["SPY"])

    def test_missing_or_garbage_status_is_failure(self):
        closed, failed = orders.classify_close_results([{"symbol": "AAPL"}, {"symbol": "SPY", "status": "n/a"}])
        self.assertEqual(closed, [])
        self.assertEqual(len(failed), 2)

    def test_non_list_response_is_total_failure_never_success(self):
        closed, failed = orders.classify_close_results("Error calling tool 'close_all_positions': boom")
        self.assertEqual(closed, [])
        self.assertEqual(failed[0]["symbol"], "*")
        self.assertIn("boom", failed[0]["body"])

    def test_empty_list_is_nothing_to_close(self):
        self.assertEqual(orders.classify_close_results([]), ([], []))


class UnprotectedPositionsTest(unittest.TestCase):
    def test_only_symbols_with_no_working_close(self):
        self.assertEqual(orders.unprotected_positions(["SPY", "AAPL", "QQQ"], {"AAPL"}), ["QQQ", "SPY"])

    def test_empty_when_all_covered(self):
        self.assertEqual(orders.unprotected_positions(["SPY"], {"SPY"}), [])


class DescribeFlattenOutcomeTest(unittest.TestCase):
    def test_flat(self):
        state, msg = orders.describe_flatten_outcome([], set(), True, 2.0, 3)
        self.assertEqual(state, orders.FLAT)
        self.assertIn("3 position(s)", msg)

    def test_incomplete_when_held_with_no_closing_order(self):
        state, msg = orders.describe_flatten_outcome(["SPY"], set(), True, 30.0, 1)
        self.assertEqual(state, orders.INCOMPLETE)
        self.assertIn("SPY", msg)

    def test_resting_when_market_closed_and_close_is_working(self):
        state, msg = orders.describe_flatten_outcome(["SPY"], {"SPY"}, False, 30.0, 1)
        self.assertEqual(state, orders.RESTING)
        self.assertIn("next open", msg)

    def test_filling_when_market_open_and_close_is_working(self):
        state, msg = orders.describe_flatten_outcome(["SPY"], {"SPY"}, True, 12.3, 1)
        self.assertEqual(state, orders.FILLING)
        self.assertIn("12s", msg)

    def test_filling_with_unknown_market_state(self):
        state, msg = orders.describe_flatten_outcome(["SPY"], {"SPY"}, None, 5.0, 1)
        self.assertEqual(state, orders.FILLING)
        self.assertIn("unknown", msg)


if __name__ == "__main__":
    unittest.main()

import unittest

import trade_report
from bot import trades

ORDERS = [
    {
        "id": "o1", "client_order_id": "hb-1", "symbol": "SPY260904C00770000", "side": "buy",
        "filled_qty": "2", "filled_avg_price": "2.00", "filled_at": "2026-09-01T14:00:00Z", "legs": None,
    },
    {
        "id": "o2", "client_order_id": "hb-2", "symbol": "SPY260904C00770000", "side": "sell",
        "filled_qty": "2", "filled_avg_price": "3.20", "filled_at": "2026-09-01T15:30:00Z",
    },
    {"id": "o3", "symbol": "QQQ", "side": "buy", "filled_qty": "0", "filled_avg_price": None, "filled_at": None},  # unfilled
    {
        "id": "parent", "symbol": "AAPL", "side": "buy", "filled_qty": "10", "filled_avg_price": "100",
        "filled_at": "2026-09-01T14:05:00Z",
        "legs": [
            {"id": "leg", "symbol": "AAPL", "side": "sell", "filled_qty": "10", "filled_avg_price": "101",
             "filled_at": "2026-09-01T19:50:00Z"},
        ],
    },
]


class FillsFromOrdersTest(unittest.TestCase):
    def test_walks_legs_skips_unfilled_and_classifies_sells(self):
        fills = trade_report.fills_from_orders(ORDERS, {"o2": "take_profit (+60.0% vs entry)"})
        self.assertEqual(len(fills), 4)
        by_id = {(f["symbol"], f["side"]): f for f in fills}
        self.assertEqual(by_id[("SPY260904C00770000", "sell")]["reason"], trades.TAKE_PROFIT)
        self.assertEqual(by_id[("AAPL", "sell")]["reason"], trades.FLATTEN)  # leg, never journaled
        self.assertIsNone(by_id[("AAPL", "buy")]["reason"])
        self.assertEqual(by_id[("SPY260904C00770000", "buy")]["price"], 2.0)

    def test_end_to_end_pairing_from_orders(self):
        fills = trade_report.fills_from_orders(ORDERS, {"o2": "take_profit"})
        trips, still_open = trades.pair_round_trips(fills)
        self.assertEqual(still_open, [])
        pnl = {t["symbol"]: t["pnl"] for t in trips}
        self.assertEqual(pnl["SPY260904C00770000"], 240.0)
        self.assertEqual(pnl["AAPL"], 10.0)

    def test_garbage_entries_are_ignored(self):
        self.assertEqual(trade_report.fills_from_orders(["junk", None, {}], {}), [])


if __name__ == "__main__":
    unittest.main()

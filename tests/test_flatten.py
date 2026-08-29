import json
import unittest

from bot import flatten, orders


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, payload):
        text = payload if isinstance(payload, str) else json.dumps({"data": {"result": payload}})
        self.content = [FakeContent(text)]


class FakeMCPClient:
    """Scripted broker. `positions` / `open_orders` are lists consumed one
    entry per poll (last entry repeats), so a test can script "still held
    for two polls, then flat"."""

    def __init__(self, positions, open_orders, close_results=None, clock_open=True):
        self.positions = list(positions)
        self.open_orders = list(open_orders)
        self.close_results = close_results if close_results is not None else []
        self.clock_open = clock_open
        self.calls = []

    def _next(self, seq):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def call_tool(self, name, arguments=None):
        self.calls.append(name)
        if name == "get_all_positions":
            return FakeResult([{"symbol": s} for s in self._next(self.positions)])
        if name == "get_orders":
            return FakeResult([{"symbol": s} for s in self._next(self.open_orders)])
        if name == "cancel_all_orders":
            return FakeResult([])
        if name == "close_all_positions":
            return FakeResult(self.close_results)
        if name == "get_clock":
            return FakeResult(json.dumps({"data": {"is_open": self.clock_open}}))
        raise AssertionError(f"unexpected tool {name}")


FAST = {"settle_timeout_sec": 0.2, "verify_timeout_sec": 0.2, "poll_sec": 0.01}


class FlattenAllTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancels_before_closing_and_reports_flat(self):
        client = FakeMCPClient(
            positions=[["SPY", "AAPL"], []],
            open_orders=[["SPY"], []],
            close_results=[{"symbol": "SPY", "status": 200}, {"symbol": "AAPL", "status": 200}],
        )
        out = await flatten.flatten_all(client, **FAST)

        self.assertEqual(out.state, orders.FLAT)
        self.assertEqual(out.attempted, ["SPY", "AAPL"])
        self.assertTrue(out.cancels_settled)
        self.assertEqual([c["symbol"] for c in out.closed], ["SPY", "AAPL"])
        self.assertEqual(out.remaining, [])
        # order of operations: cancel, then (poll) orders, then close
        self.assertLess(client.calls.index("cancel_all_orders"), client.calls.index("close_all_positions"))
        self.assertLess(client.calls.index("get_orders"), client.calls.index("close_all_positions"))

    async def test_waits_for_fills_then_flat(self):
        client = FakeMCPClient(
            positions=[["SPY"], ["SPY"], ["SPY"], []],  # still held for a few polls, then flat
            open_orders=[[]],
            close_results=[{"symbol": "SPY", "status": 200}],
        )
        out = await flatten.flatten_all(client, **FAST)
        self.assertEqual(out.state, orders.FLAT)
        self.assertGreater(client.calls.count("get_all_positions"), 2)

    async def test_incomplete_when_still_held_with_no_working_close(self):
        client = FakeMCPClient(
            positions=[["SPY"]],
            open_orders=[[]],
            close_results=[{"symbol": "SPY", "status": 422, "body": "insufficient qty"}],
        )
        out = await flatten.flatten_all(client, **FAST)
        self.assertEqual(out.state, orders.INCOMPLETE)
        self.assertEqual(out.unprotected, ["SPY"])
        self.assertEqual([f["symbol"] for f in out.failed], ["SPY"])

    async def test_resting_when_market_closed_and_close_working(self):
        client = FakeMCPClient(
            positions=[["SPY"]],
            open_orders=[[], ["SPY"]],  # settled (empty) during cancel wait; then the close order shows
            close_results=[{"symbol": "SPY", "status": 200}],
            clock_open=False,
        )
        out = await flatten.flatten_all(client, **FAST)
        self.assertEqual(out.state, orders.RESTING)
        self.assertEqual(out.pending_close, ["SPY"])
        self.assertFalse(out.market_open)

    async def test_cancels_not_settled_is_reported_not_fatal(self):
        client = FakeMCPClient(
            positions=[["SPY"], []],
            open_orders=[["SPY"]],  # never clears
            close_results=[{"symbol": "SPY", "status": 200}],
        )
        out = await flatten.flatten_all(client, **FAST)
        self.assertFalse(out.cancels_settled)
        self.assertEqual(out.state, orders.FLAT)

    async def test_non_json_close_response_is_total_failure(self):
        client = FakeMCPClient(
            positions=[["SPY"]],
            open_orders=[[]],
            close_results="Error calling tool 'close_all_positions': ConnectError",
        )
        client.close_results = "Error calling tool 'close_all_positions': ConnectError"
        out = await flatten.flatten_all(client, **FAST)
        self.assertEqual(out.failed[0]["symbol"], "*")
        self.assertEqual(out.state, orders.INCOMPLETE)


if __name__ == "__main__":
    unittest.main()

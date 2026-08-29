import json
import unittest

from bot import research


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, payload):
        self.content = [FakeContent(payload if isinstance(payload, str) else json.dumps({"data": payload}))]


class FakeMCP:
    def __init__(self, response=None, raise_exc=None):
        self.response = response if response is not None else {"ok": True}
        self.raise_exc = raise_exc
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        if self.raise_exc:
            raise self.raise_exc
        return FakeResult(self.response)


class ToMcpCallTest(unittest.TestCase):
    def test_bars_forces_iex_feed_and_bounds(self):
        name, args = research.to_mcp_call("get_bars", {"symbol": "spy", "timeframe": "15Min", "lookback_hours": 999})
        self.assertEqual(name, "get_stock_bars")
        self.assertEqual(args["symbols"], "SPY")
        self.assertEqual(args["feed"], "iex")
        self.assertEqual(args["days"], 22)  # 120h capped -> 20 sessions-ish + weekend margin
        self.assertEqual(args["limit"], 120)
        self.assertEqual(args["sort"], "desc")

    def test_bars_short_lookback_still_spans_a_weekend(self):
        # A "9 hour" lookback on a Saturday returned {} live: wall-clock hours
        # on Alpaca's side. Always ask for at least 3 calendar days.
        _, args = research.to_mcp_call("get_bars", {"symbol": "SPY", "timeframe": "15Min", "lookback_hours": 9})
        self.assertGreaterEqual(args["days"], 3)
        self.assertNotIn("hours", args)

    def test_bars_keep_only_the_newest_max_bars_so_json_stays_intact(self):
        bars = [{"t": f"2026-09-01T{h:02d}:{m:02d}:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}
                for h in range(9, 16) for m in (0, 15, 30, 45)]  # 28 bars
        bars += [{"t": f"2026-09-02T{h:02d}:{m:02d}:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}
                 for h in range(9, 16) for m in (0, 15, 30, 45)]  # 56 total
        out = research.result_text(FakeResult({"bars": {"SPY": bars}}), "get_bars")
        parsed = json.loads(out)  # must not be truncated mid-JSON
        self.assertEqual(len(parsed["SPY"]), research.MAX_BARS)
        self.assertTrue(parsed["SPY"][-1]["t"].startswith("2026-09-02T15:45"))

    def test_bars_trimmed_are_oldest_first_even_when_fetched_newest_first(self):
        payload = {"bars": {"SPY": [{"t": "2026-09-01T15:00:00Z", "c": 2}, {"t": "2026-09-01T14:00:00Z", "c": 1}]}}
        out = json.loads(research.result_text(FakeResult(payload), "get_bars"))
        self.assertEqual([b["c"] for b in out["SPY"]], [1, 2])

    def test_bars_bad_timeframe_falls_back(self):
        _, args = research.to_mcp_call("get_bars", {"symbol": "SPY", "timeframe": "1Sec"})
        self.assertEqual(args["timeframe"], "15Min")

    def test_option_snapshot_forces_indicative_and_caps_symbols(self):
        syms = ",".join(f"SPY260904C0077{i}000" for i in range(15))
        name, args = research.to_mcp_call("get_option_snapshot", {"symbols": syms})
        self.assertEqual(name, "get_option_snapshot")
        self.assertEqual(args["feed"], "indicative")
        self.assertEqual(len(args["symbols"].split(",")), 10)

    def test_news_limit_bounded(self):
        _, args = research.to_mcp_call("get_news", {"symbols": "AAPL,MSFT", "limit": 50})
        self.assertEqual(args["limit"], 10)
        self.assertFalse(args["include_content"])

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ValueError):
            research.to_mcp_call("place_option_order", {"symbol": "SPY"})
        with self.assertRaises(ValueError):
            research.to_mcp_call("close_all_positions", {})


class ResultTextTest(unittest.TestCase):
    def test_bars_trimmed_to_ohlcv(self):
        payload = {"bars": {"SPY": [{"t": "2026-09-01T14:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "n": 9, "vw": 1.2}]}}
        out = json.loads(research.result_text(FakeResult(payload), "get_bars"))
        self.assertEqual(out, {"SPY": [{"t": "2026-09-01T14:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]})

    def test_news_trimmed(self):
        payload = {"news": [{"headline": "h", "summary": "s", "symbols": ["SPY"], "created_at": "t", "source": "x", "content": "long" * 1000}]}
        out = json.loads(research.result_text(FakeResult(payload), "get_news"))
        self.assertNotIn("content", out[0])

    def test_long_results_truncated(self):
        payload = {"snapshots": {f"S{i}": {"x": "y" * 100} for i in range(200)}}
        out = research.result_text(FakeResult(payload), "get_option_snapshot")
        self.assertLessEqual(len(out), research.MAX_RESULT_CHARS + 20)
        self.assertTrue(out.endswith('(truncated)'))

    def test_non_json_passthrough(self):
        self.assertEqual(research.result_text(FakeResult("Error calling tool 'x': boom"), "get_bars"), "Error calling tool 'x': boom")


class ExecuteToolCallTest(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_calls_mcp_with_safe_args(self):
        mcp = FakeMCP({"quotes": {"SPY": {"bp": 1, "ap": 2}}})
        out = await research.execute_tool_call(mcp, "get_stock_snapshot", {"symbol": "spy"})
        self.assertEqual(mcp.calls, [("get_stock_snapshot", {"symbols": "SPY", "feed": "iex"})])
        self.assertIn('"quotes"', out)

    async def test_disallowed_tool_returns_error_text_not_exception(self):
        mcp = FakeMCP()
        out = json.loads(await research.execute_tool_call(mcp, "place_stock_order", {"symbol": "SPY"}))
        self.assertIn("not available", out["error"])
        self.assertEqual(mcp.calls, [])

    async def test_mcp_exception_becomes_error_text(self):
        mcp = FakeMCP(raise_exc=RuntimeError("down"))
        out = json.loads(await research.execute_tool_call(mcp, "get_news", {"symbols": "SPY"}))
        self.assertIn("RuntimeError: down", out["error"])


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from datetime import date, datetime

from bot import snapshot
from bot.models import Proposal
from bot.risk import EASTERN

CLOCK_RESPONSE = {
    "data": {
        "is_open": True,
        "next_open": "2026-01-16T09:30:00-05:00",
        "next_close": "2026-01-15T16:00:00-05:00",
    }
}

ACCOUNT_RESPONSE = {
    "data": {
        "equity": "100000",
        "last_equity": "99500",
        "cash": "50000",
    }
}

EMPTY_POSITIONS_RESPONSE = {"data": {"result": []}}

STOCK_POSITION_RESPONSE = {
    "data": {
        "result": [
            {"symbol": "AAPL", "asset_class": "us_equity", "qty": "10", "market_value": "1500.00"},
        ]
    }
}

OPTION_POSITION_RESPONSE = {
    "data": {
        "result": [
            {
                "symbol": "AAPL260204C00200000",
                "asset_class": "us_option",
                "qty": "2",
                "market_value": "400.00",
            },
        ]
    }
}

SHORT_STOCK_POSITION_RESPONSE = {
    "data": {
        "result": [
            {"symbol": "AAPL", "asset_class": "us_equity", "qty": "-5", "market_value": "-750.00"},
        ]
    }
}


OPTION_CHAIN_RESPONSE = {
    "data": {
        "snapshots": {
            "AAPL260204C00200000": {
                "latestQuote": {"ap": 5.5, "bp": 5.2},
                "latestTrade": {"p": 5.35},
                "dailyBar": {"c": 5.3, "h": 5.6, "l": 5.1, "v": 120},
            }
        }
    }
}

STOCK_QUOTE_RESPONSE = {"data": {"quotes": {"AAPL": {"bp": 199.5, "ap": 200.5}}}}


def _stock_quote_response(symbol: str, bp: float, ap: float) -> dict:
    return {"data": {"quotes": {symbol: {"bp": bp, "ap": ap}}}}


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, text):
        self.content = [FakeContent(text)]


class FakeMCPClient:
    """Hand-written stub, not a mocking library — mirrors test_execute.py's
    FakeMCPClient. Maps tool name -> canned response (or a callable taking
    the call arguments, for tools called once per underlying with a symbol-
    dependent answer), since a single snapshot build calls multiple tools."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        response = self.responses[name]
        if callable(response):
            response = response(arguments)
        return FakeResult(json.dumps(response))


class BuildPositionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_positions(self):
        client = FakeMCPClient({"get_all_positions": EMPTY_POSITIONS_RESPONSE})
        positions = await snapshot.build_positions(client)
        self.assertEqual(positions, {})

    async def test_stock_position_parsed_correctly(self):
        client = FakeMCPClient({"get_all_positions": STOCK_POSITION_RESPONSE})
        positions = await snapshot.build_positions(client)

        pos = positions["AAPL"]
        self.assertEqual(pos.instrument, "stock")
        self.assertEqual(pos.qty, 10.0)
        self.assertEqual(pos.market_value, 1500.0)
        self.assertIsNone(pos.underlying)

    async def test_option_position_derives_underlying_from_occ_symbol(self):
        client = FakeMCPClient({"get_all_positions": OPTION_POSITION_RESPONSE})
        positions = await snapshot.build_positions(client)

        pos = positions["AAPL260204C00200000"]
        self.assertEqual(pos.instrument, "option")
        self.assertEqual(pos.underlying, "AAPL")
        self.assertEqual(pos.qty, 2.0)
        self.assertEqual(pos.market_value, 400.0)

    async def test_short_position_qty_and_value_are_absolute_magnitudes(self):
        client = FakeMCPClient({"get_all_positions": SHORT_STOCK_POSITION_RESPONSE})
        positions = await snapshot.build_positions(client)

        self.assertEqual(positions["AAPL"].qty, 5.0)
        self.assertEqual(positions["AAPL"].market_value, 750.0)

    async def test_option_with_malformed_symbol_gets_no_underlying_not_a_crash(self):
        response = {
            "data": {
                "result": [
                    {"symbol": "not-occ", "asset_class": "us_option", "qty": "1", "market_value": "10"},
                ]
            }
        }
        client = FakeMCPClient({"get_all_positions": response})
        positions = await snapshot.build_positions(client)
        self.assertIsNone(positions["not-occ"].underlying)


class BuildAccountStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_parses_equity_cash_and_start_of_day_equity_from_last_equity(self):
        client = FakeMCPClient(
            {"get_account_info": ACCOUNT_RESPONSE, "get_all_positions": EMPTY_POSITIONS_RESPONSE}
        )
        account = await snapshot.build_account_state(client)

        self.assertEqual(account.equity, 100000.0)
        self.assertEqual(account.start_of_day_equity, 99500.0)
        self.assertEqual(account.cash, 50000.0)
        self.assertEqual(account.positions, {})

    async def test_includes_positions_from_build_positions(self):
        client = FakeMCPClient(
            {"get_account_info": ACCOUNT_RESPONSE, "get_all_positions": STOCK_POSITION_RESPONSE}
        )
        account = await snapshot.build_account_state(client)

        self.assertEqual(account.open_position_count, 1)
        self.assertIn("AAPL", account.positions)


def _research_config(**overrides):
    config = {
        "underlyings": ["AAPL"],
        "min_days_to_expiration": 1,
        "max_days_to_expiration": 45,
        "option_strike_band_pct": 0.08,
    }
    config.update(overrides)
    return config


class GetUnderlyingPriceTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_mid_of_bid_ask(self):
        client = FakeMCPClient({"get_stock_latest_quote": STOCK_QUOTE_RESPONSE})
        price = await snapshot.get_underlying_price(client, "AAPL")
        self.assertEqual(price, 200.0)  # mid(199.5, 200.5)


class BuildOptionResearchTest(unittest.IsolatedAsyncioTestCase):
    def _client(self, price_bp=199.5, price_ap=200.5):
        return FakeMCPClient(
            {
                "get_stock_latest_quote": _stock_quote_response("AAPL", price_bp, price_ap),
                "get_option_chain": OPTION_CHAIN_RESPONSE,
            }
        )

    async def test_fetches_chain_with_indicative_feed_not_opra(self):
        # feed="opra" 403s for accounts without an OPRA subscription
        # (confirmed live) — this must always request "indicative".
        client = self._client()
        research = await snapshot.build_option_research(
            client, _research_config(), today=date(2026, 1, 15)
        )

        self.assertIn("AAPL", research)
        self.assertEqual(research["AAPL"]["underlying_price"], 200.0)
        self.assertIn("AAPL260204C00200000", research["AAPL"]["contracts"])
        tool_name, args = client.calls[1]
        self.assertEqual(tool_name, "get_option_chain")
        self.assertEqual(args["underlying_symbol"], "AAPL")
        self.assertEqual(args["feed"], "indicative")

    async def test_strike_band_derived_from_underlying_price_and_config(self):
        # Regression: an unbounded chain request returns strike-ascending
        # results, which for a high-priced underlying is deep OTM calls
        # only and zero puts (confirmed live against real SPY data).
        client = self._client()
        await snapshot.build_option_research(
            client, _research_config(option_strike_band_pct=0.10), today=date(2026, 1, 15)
        )

        _, args = client.calls[1]
        self.assertEqual(args["strike_price_gte"], 180.0)  # 200 * 0.90
        self.assertEqual(args["strike_price_lte"], 220.0)  # 200 * 1.10

    async def test_expiration_window_derived_from_config_and_today(self):
        client = self._client()
        await snapshot.build_option_research(client, _research_config(), today=date(2026, 1, 15))

        _, args = client.calls[1]
        self.assertEqual(args["expiration_date_gte"], "2026-01-16")  # today + min(1)
        self.assertEqual(args["expiration_date_lte"], "2026-03-01")  # today + max(45)

    async def test_calls_once_per_underlying_in_whitelist(self):
        def quote_for(arguments):
            return _stock_quote_response(arguments["symbols"], 100.0, 101.0)

        client = FakeMCPClient(
            {"get_stock_latest_quote": quote_for, "get_option_chain": OPTION_CHAIN_RESPONSE}
        )
        config = _research_config(underlyings=["AAPL", "SPY", "QQQ"])

        research = await snapshot.build_option_research(client, config, today=date(2026, 1, 15))

        option_chain_calls = [args for name, args in client.calls if name == "get_option_chain"]
        self.assertEqual(len(option_chain_calls), 3)
        called_symbols = [args["underlying_symbol"] for args in option_chain_calls]
        self.assertEqual(called_symbols, ["AAPL", "SPY", "QQQ"])
        self.assertEqual(set(research.keys()), {"AAPL", "SPY", "QQQ"})

    async def test_underlying_with_no_contracts_gets_empty_dict_not_a_crash(self):
        client = FakeMCPClient(
            {
                "get_stock_latest_quote": STOCK_QUOTE_RESPONSE,
                "get_option_chain": {"data": {"snapshots": {}}},
            }
        )
        research = await snapshot.build_option_research(
            client, _research_config(), today=date(2026, 1, 15)
        )
        self.assertEqual(research["AAPL"]["contracts"], {})


PRICE_SNAPSHOT = {
    "options": {
        "AAPL": {
            "underlying_price": 200.0,
            "contracts": {
                "AAPL260204C00200000": {"latestQuote": {"bp": 5.0, "ap": 6.0}},
                "AAPL260204P00200000": {"latestQuote": {"bp": 0, "ap": 0}, "latestTrade": {"p": 4.4}},
                "AAPL260204C00210000": {},
            },
        }
    }
}


class PriceForProposalTest(unittest.TestCase):
    SNAP = PRICE_SNAPSHOT

    def test_option_uses_bid_ask_mid(self):
        p = Proposal("option", "AAPL260204C00200000", "buy", 1, underlying="AAPL")
        self.assertEqual(snapshot.price_for_proposal(self.SNAP, p), 5.5)

    def test_option_falls_back_to_last_trade_when_quote_empty(self):
        p = Proposal("option", "AAPL260204P00200000", "buy", 1, underlying="AAPL")
        self.assertEqual(snapshot.price_for_proposal(self.SNAP, p), 4.4)

    def test_option_with_no_price_data_returns_none(self):
        p = Proposal("option", "AAPL260204C00210000", "buy", 1, underlying="AAPL")
        self.assertIsNone(snapshot.price_for_proposal(self.SNAP, p))

    def test_option_not_in_snapshot_returns_none(self):
        p = Proposal("option", "AAPL260204C00999000", "buy", 1, underlying="AAPL")
        self.assertIsNone(snapshot.price_for_proposal(self.SNAP, p))

    def test_stock_uses_underlying_price(self):
        p = Proposal("stock", "AAPL", "buy", 1)
        self.assertEqual(snapshot.price_for_proposal(self.SNAP, p), 200.0)

    def test_stock_not_in_snapshot_returns_none(self):
        p = Proposal("stock", "TSLA", "buy", 1)
        self.assertIsNone(snapshot.price_for_proposal(self.SNAP, p))


class BuildSnapshotTest(unittest.IsolatedAsyncioTestCase):
    def _client(self, positions=STOCK_POSITION_RESPONSE):
        return FakeMCPClient(
            {
                "get_clock": CLOCK_RESPONSE,
                "get_account_info": ACCOUNT_RESPONSE,
                "get_all_positions": positions,
                "get_stock_latest_quote": STOCK_QUOTE_RESPONSE,
                "get_option_chain": OPTION_CHAIN_RESPONSE,
            }
        )

    async def test_assembles_clock_account_and_options(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        snap = await snapshot.build_snapshot(self._client(), _research_config(), now=now)

        self.assertTrue(snap["market_open"])
        self.assertEqual(snap["next_close"], "2026-01-15T16:00:00-05:00")
        self.assertEqual(snap["account"]["equity"], 100000.0)
        self.assertEqual(snap["account"]["start_of_day_equity"], 99500.0)
        self.assertEqual(len(snap["account"]["positions"]), 1)
        self.assertEqual(snap["account"]["positions"][0]["symbol"], "AAPL")
        self.assertIn("AAPL", snap["options"])
        self.assertEqual(snap["options"]["AAPL"]["underlying_price"], 200.0)
        self.assertIn("AAPL260204C00200000", snap["options"]["AAPL"]["contracts"])

    async def test_snapshot_is_json_serializable(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        snap = await snapshot.build_snapshot(
            self._client(positions=EMPTY_POSITIONS_RESPONSE), _research_config(), now=now
        )
        json.dumps(snap)  # must not raise

    async def test_empty_account_has_empty_positions_list_not_a_crash(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        snap = await snapshot.build_snapshot(
            self._client(positions=EMPTY_POSITIONS_RESPONSE), _research_config(), now=now
        )
        self.assertEqual(snap["account"]["positions"], [])


if __name__ == "__main__":
    unittest.main()

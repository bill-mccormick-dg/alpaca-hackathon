import json
import unittest
from datetime import date

from bot import snapshot

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


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, text):
        self.content = [FakeContent(text)]


class FakeMCPClient:
    """Hand-written stub, not a mocking library — mirrors test_execute.py's
    FakeMCPClient. Maps tool name -> canned response, since a single
    snapshot build calls multiple tools."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        return FakeResult(json.dumps(self.responses[name]))


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
    }
    config.update(overrides)
    return config


class BuildOptionResearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_chain_with_indicative_feed_not_opra(self):
        # feed="opra" 403s for accounts without an OPRA subscription
        # (confirmed live) — this must always request "indicative".
        client = FakeMCPClient({"get_option_chain": OPTION_CHAIN_RESPONSE})
        research = await snapshot.build_option_research(
            client, _research_config(), today=date(2026, 1, 15)
        )

        self.assertIn("AAPL", research)
        self.assertIn("AAPL260204C00200000", research["AAPL"])
        tool_name, args = client.calls[0]
        self.assertEqual(tool_name, "get_option_chain")
        self.assertEqual(args["underlying_symbol"], "AAPL")
        self.assertEqual(args["feed"], "indicative")

    async def test_expiration_window_derived_from_config_and_today(self):
        client = FakeMCPClient({"get_option_chain": OPTION_CHAIN_RESPONSE})
        await snapshot.build_option_research(client, _research_config(), today=date(2026, 1, 15))

        _, args = client.calls[0]
        self.assertEqual(args["expiration_date_gte"], "2026-01-16")  # today + min(1)
        self.assertEqual(args["expiration_date_lte"], "2026-03-01")  # today + max(45)

    async def test_calls_once_per_underlying_in_whitelist(self):
        client = FakeMCPClient({"get_option_chain": OPTION_CHAIN_RESPONSE})
        config = _research_config(underlyings=["AAPL", "SPY", "QQQ"])

        research = await snapshot.build_option_research(client, config, today=date(2026, 1, 15))

        self.assertEqual(len(client.calls), 3)
        called_symbols = [args["underlying_symbol"] for _, args in client.calls]
        self.assertEqual(called_symbols, ["AAPL", "SPY", "QQQ"])
        self.assertEqual(set(research.keys()), {"AAPL", "SPY", "QQQ"})

    async def test_underlying_with_no_contracts_gets_empty_dict_not_a_crash(self):
        client = FakeMCPClient({"get_option_chain": {"data": {"snapshots": {}}}})
        research = await snapshot.build_option_research(
            client, _research_config(), today=date(2026, 1, 15)
        )
        self.assertEqual(research["AAPL"], {})


if __name__ == "__main__":
    unittest.main()

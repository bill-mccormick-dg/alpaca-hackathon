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

NO_OPEN_ORDERS_RESPONSE = {"data": {"result": []}}

OPEN_ORDERS_RESPONSE = {
    "data": {
        "result": [
            {
                "id": "6b4e2f3a-0000-4000-8000-000000000001",
                "client_order_id": "hb-20260901-120017-QQQ260903P00709000-buy",
                "symbol": "QQQ260903P00709000", "asset_class": "us_option", "side": "buy",
                "qty": "4", "filled_qty": "0", "type": "limit", "limit_price": "3.56",
                "submitted_at": "2026-09-01T16:00:17.123456Z", "status": "new",
            },
            {"id": "x", "symbol": "AAPL", "asset_class": "us_equity", "side": "sell", "qty": "10", "filled_qty": "4",
             "type": "market", "limit_price": None, "created_at": "2026-09-01T16:05:00Z"},
            {"no": "symbol here"},
        ]
    }
}

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


class DataUnwrapTest(unittest.TestCase):
    def test_unwraps_data_envelope(self):
        self.assertEqual(snapshot._data(FakeResult('{"data": {"x": 1}}')), {"x": 1})

    def test_non_json_tool_error_text_surfaces_as_runtime_error(self):
        # The MCP server reports upstream failures as plain text, not JSON.
        result = FakeResult("Error calling tool 'get_clock': Request error (ConnectError): boom")
        with self.assertRaises(RuntimeError) as ctx:
            snapshot._data(result)
        self.assertIn("get_clock", str(ctx.exception))
        self.assertIn("ConnectError", str(ctx.exception))


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

    async def test_carries_entry_and_current_price_for_exit_rules(self):
        response = {
            "data": {
                "result": [
                    {"symbol": "AAPL", "asset_class": "us_equity", "qty": "10", "market_value": "1500",
                     "avg_entry_price": "140.25", "current_price": "150.00"},
                    {"symbol": "SPY", "asset_class": "us_equity", "qty": "1", "market_value": "1"},
                ]
            }
        }
        positions = await snapshot.build_positions(FakeMCPClient({"get_all_positions": response}))
        self.assertEqual(positions["AAPL"].avg_entry_price, 140.25)
        self.assertEqual(positions["AAPL"].current_price, 150.0)
        self.assertIsNone(positions["SPY"].avg_entry_price)  # missing -> None, not a crash

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

    async def test_reads_the_brokers_account_number(self):
        """bot/identity.py's guard is only as good as this field arriving."""
        client = FakeMCPClient(
            {
                "get_account_info": {"data": {**ACCOUNT_RESPONSE["data"], "account_number": "PA3VS39Y5LE2"}},
                "get_all_positions": EMPTY_POSITIONS_RESPONSE,
            }
        )
        account = await snapshot.build_account_state(client)

        self.assertEqual(account.account_number, "PA3VS39Y5LE2")

    async def test_account_number_is_none_when_the_broker_omits_it(self):
        client = FakeMCPClient(
            {"get_account_info": ACCOUNT_RESPONSE, "get_all_positions": EMPTY_POSITIONS_RESPONSE}
        )
        account = await snapshot.build_account_state(client)

        self.assertIsNone(account.account_number)

    async def test_fetch_account_number_reads_it_without_a_snapshot(self):
        client = FakeMCPClient(
            {"get_account_info": {"data": {**ACCOUNT_RESPONSE["data"], "account_number": "PA9TEST00001"}}}
        )

        self.assertEqual(await snapshot.fetch_account_number(client), "PA9TEST00001")

    async def test_account_number_survives_the_snapshot_round_trip(self):
        """run_cycle rebuilds AccountState from the serialized snapshot; if the
        number were dropped there, every challenger cycle would fail closed."""
        import run_cycle

        client = FakeMCPClient(
            {
                "get_account_info": {"data": {**ACCOUNT_RESPONSE["data"], "account_number": "PA9TEST00001"}},
                "get_all_positions": EMPTY_POSITIONS_RESPONSE,
            }
        )
        account = await snapshot.build_account_state(client)
        rebuilt = run_cycle.account_from_snapshot({"account": snapshot._serialize_account(account)})

        self.assertEqual(rebuilt.account_number, "PA9TEST00001")

    async def test_includes_positions_from_build_positions(self):
        client = FakeMCPClient(
            {"get_account_info": ACCOUNT_RESPONSE, "get_all_positions": STOCK_POSITION_RESPONSE}
        )
        account = await snapshot.build_account_state(client)

        self.assertEqual(account.open_position_count, 1)
        self.assertIn("AAPL", account.positions)


class OneSidedQuoteTest(unittest.IsolatedAsyncioTestCase):
    """The mid of a zero bid and a real ask is half the price. After hours
    IEX prints exactly that, and a strike band centred on half of SPY
    fetches a chain nobody trades."""

    async def test_a_zero_bid_means_the_ask_is_the_price(self):
        client = FakeMCPClient({"get_stock_latest_quote": _stock_quote_response("SPY", 0, 761.85)})
        self.assertEqual(await snapshot.get_underlying_price(client, "SPY"), 761.85)

    async def test_a_missing_ask_means_the_bid_is_the_price(self):
        client = FakeMCPClient({"get_stock_latest_quote": {"data": {"quotes": {"SPY": {"bp": 761.5, "ap": None}}}}})
        self.assertEqual(await snapshot.get_underlying_price(client, "SPY"), 761.5)

    async def test_two_sided_is_still_the_mid(self):
        client = FakeMCPClient({"get_stock_latest_quote": _stock_quote_response("SPY", 761.5, 761.9)})
        self.assertAlmostEqual(await snapshot.get_underlying_price(client, "SPY"), 761.7)

    async def test_no_sides_at_all_raises(self):
        client = FakeMCPClient({"get_stock_latest_quote": _stock_quote_response("SPY", 0, 0)})
        with self.assertRaises(RuntimeError):
            await snapshot.get_underlying_price(client, "SPY")


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


def _page(symbols, next_token=None):
    """One get_option_chain page in the API's own shape: `snapshots` plus a
    `next_page_token` that is null on the last page."""
    return {
        "data": {
            "snapshots": {s: {"latestQuote": {"ap": 5.5, "bp": 5.2}} for s in symbols},
            "next_page_token": next_token,
        }
    }


class ChainPaginationTest(unittest.IsolatedAsyncioTestCase):
    """Issue #158: one page covers 1-3 DTE on SPY/QQQ. The API's
    next_page_token is the truncation signal; follow it until null."""

    TODAY = date(2026, 1, 15)

    def _client(self, pages_by_token: dict):
        def chain(arguments):
            return pages_by_token[arguments.get("page_token")]

        return FakeMCPClient({"get_stock_latest_quote": STOCK_QUOTE_RESPONSE, "get_option_chain": chain})

    def _chain_calls(self, client):
        return [args for name, args in client.calls if name == "get_option_chain"]

    async def test_single_page_without_a_token_key_is_one_call(self):
        # The legacy fixture omits next_page_token entirely - must mean "done".
        client = FakeMCPClient({"get_stock_latest_quote": STOCK_QUOTE_RESPONSE, "get_option_chain": OPTION_CHAIN_RESPONSE})

        research = await snapshot.build_option_research(client, _research_config(), today=self.TODAY)

        self.assertEqual(len(self._chain_calls(client)), 1)
        self.assertEqual(research["AAPL"]["pages"], 1)
        self.assertFalse(research["AAPL"]["truncated"])

    async def test_requests_the_api_maximum_page_size(self):
        client = self._client({None: _page(["AAPL260204C00200000"])})

        await snapshot.build_option_research(client, _research_config(), today=self.TODAY)

        self.assertEqual(self._chain_calls(client)[0]["limit"], snapshot.CHAIN_PAGE_LIMIT)
        self.assertEqual(snapshot.CHAIN_PAGE_LIMIT, 1000)

    async def test_follows_the_token_and_merges_pages_with_identical_filters(self):
        client = self._client({
            None: _page(["AAPL260116C00200000", "AAPL260117C00200000"], next_token="t2"),
            "t2": _page(["AAPL260227P00200000"], next_token=None),
        })

        research = await snapshot.build_option_research(client, _research_config(), today=self.TODAY)

        calls = self._chain_calls(client)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("page_token", calls[0])
        self.assertEqual(calls[1]["page_token"], "t2")
        for key in ("underlying_symbol", "feed", "expiration_date_gte", "expiration_date_lte",
                    "strike_price_gte", "strike_price_lte", "limit"):
            self.assertEqual(calls[0][key], calls[1][key], key)
        self.assertEqual(len(research["AAPL"]["contracts"]), 3)
        self.assertEqual(research["AAPL"]["pages"], 2)
        self.assertFalse(research["AAPL"]["truncated"])
        self.assertEqual(research["AAPL"]["max_dte"], (date(2026, 2, 27) - self.TODAY).days)

    async def test_stops_at_the_cap_and_says_so(self):
        # Every page hands back a fresh token: an endless chain.
        counter = {"n": 0}

        def chain(arguments):
            counter["n"] += 1
            return _page([f"AAPL2602{counter['n']:02d}C00200000"], next_token=f"t{counter['n']}")

        client = FakeMCPClient({"get_stock_latest_quote": STOCK_QUOTE_RESPONSE, "get_option_chain": chain})

        research = await snapshot.build_option_research(client, _research_config(), today=self.TODAY)

        self.assertEqual(len(self._chain_calls(client)), snapshot.CHAIN_MAX_PAGES)
        self.assertEqual(research["AAPL"]["pages"], snapshot.CHAIN_MAX_PAGES)
        self.assertTrue(research["AAPL"]["truncated"])

    async def test_a_repeated_token_breaks_the_loop(self):
        client = self._client({
            None: _page(["AAPL260204C00200000"], next_token="same"),
            "same": _page(["AAPL260205C00200000"], next_token="same"),
        })

        research = await snapshot.build_option_research(client, _research_config(), today=self.TODAY)

        self.assertEqual(len(self._chain_calls(client)), 2)
        self.assertFalse(research["AAPL"]["truncated"])

    async def test_an_empty_page_breaks_the_loop(self):
        client = self._client({
            None: _page(["AAPL260204C00200000"], next_token="t2"),
            "t2": _page([], next_token="t3"),
        })

        research = await snapshot.build_option_research(client, _research_config(), today=self.TODAY)

        self.assertEqual(len(self._chain_calls(client)), 2)
        self.assertEqual(len(research["AAPL"]["contracts"]), 1)

    def test_chain_coverage_is_the_journal_shape(self):
        options = {
            "SPY": {"underlying_price": 767.0, "contracts": {"a": {}, "b": {}}, "pages": 8, "truncated": False, "max_dte": 45},
            "QQQ": {"underlying_price": 717.0, "contracts": {}},
        }

        cov = snapshot.chain_coverage(options)

        self.assertEqual(cov["SPY"], {"contracts": 2, "pages": 8, "max_dte": 45, "truncated": False})
        self.assertEqual(cov["QQQ"], {"contracts": 0, "pages": None, "max_dte": None, "truncated": False})


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


class QuoteOptionMidTest(unittest.IsolatedAsyncioTestCase):
    """The pricing path for a contract the snapshot's page did not include -
    the 4-DTE SPY put the test account researched and proposed on
    2026-08-31, which the funnel rejected as "price must be positive"."""

    def _client(self, quotes):
        return FakeMCPClient({"get_option_latest_quote": {"data": {"quotes": quotes}}})

    async def test_mid_of_a_two_sided_quote(self):
        client = self._client({"SPY260904P00766000": {"bp": 3.82, "ap": 3.89}})
        self.assertAlmostEqual(await snapshot.quote_option_mid(client, "SPY260904P00766000"), 3.855)
        name, args = client.calls[0]
        self.assertEqual(name, "get_option_latest_quote")
        self.assertEqual(args["symbols"], "SPY260904P00766000")
        self.assertEqual(args["feed"], "indicative")

    async def test_one_sided_quote_is_unpriceable(self):
        client = self._client({"SPY260904P00766000": {"bp": 0, "ap": 3.89}})
        self.assertIsNone(await snapshot.quote_option_mid(client, "SPY260904P00766000"))

    async def test_symbol_the_broker_does_not_know_is_unpriceable(self):
        """An invented symbol must still be rejected downstream, so it must
        not get a price here."""
        client = self._client({})
        self.assertIsNone(await snapshot.quote_option_mid(client, "SPY260904P00999000"))

    async def test_tool_failure_is_unpriceable_not_a_crash(self):
        def boom(_args):
            raise RuntimeError("HTTP 400")
        client = FakeMCPClient({"get_option_latest_quote": boom})
        self.assertIsNone(await snapshot.quote_option_mid(client, "SPY260904P00766000"))


class BuildOpenOrdersTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalises_the_brokers_strings(self):
        out = await snapshot.build_open_orders(FakeMCPClient({"get_orders": OPEN_ORDERS_RESPONSE}))
        self.assertEqual(out[0], {
            "id": "6b4e2f3a-0000-4000-8000-000000000001",
            "client_order_id": "hb-20260901-120017-QQQ260903P00709000-buy",
            "symbol": "QQQ260903P00709000", "side": "buy", "qty": 4.0, "filled_qty": 0.0,
            "order_type": "limit", "limit_price": 3.56, "submitted_at": "2026-09-01T16:00:17.123456Z",
            "instrument": "option",
        })
        # a market order has no limit; created_at stands in for submitted_at;
        # an entry with no symbol is dropped rather than crashing the cycle
        self.assertEqual((out[1]["limit_price"], out[1]["filled_qty"], out[1]["instrument"], out[1]["submitted_at"]),
                         (None, 4.0, "stock", "2026-09-01T16:05:00Z"))
        self.assertEqual(len(out), 2)

    async def test_a_failed_lookup_is_none_not_an_empty_list(self):
        def boom(arguments):
            raise RuntimeError("MCP down")

        self.assertIsNone(await snapshot.build_open_orders(FakeMCPClient({"get_orders": boom})))
        self.assertIsNone(await snapshot.build_open_orders(FakeMCPClient({"get_orders": {"data": "Error calling tool"}})))


class BuildSnapshotTest(unittest.IsolatedAsyncioTestCase):
    def _client(self, positions=STOCK_POSITION_RESPONSE):
        return FakeMCPClient(
            {
                "get_clock": CLOCK_RESPONSE,
                "get_account_info": ACCOUNT_RESPONSE,
                "get_all_positions": positions,
                "get_stock_latest_quote": STOCK_QUOTE_RESPONSE,
                "get_option_chain": OPTION_CHAIN_RESPONSE,
                "get_orders": NO_OPEN_ORDERS_RESPONSE,
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

    async def test_prior_close_rides_on_every_underlying(self):
        """#226 lever 1: yesterday's close for the exit-claim audit, fetched
        for the whole whitelist in one call rather than only for SPY/QQQ."""
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        client = self._client()
        client.responses["get_stock_snapshot"] = {"snapshots": {"AAPL": {"prevDailyBar": {"c": 198.25, "h": 201.0}}}}
        snap = await snapshot.build_snapshot(client, _research_config(), now=now)

        self.assertEqual(snap["options"]["AAPL"]["prior_close"], 198.25)
        self.assertEqual(("get_stock_snapshot", {"symbols": "AAPL", "feed": "iex"}),
                         next(c for c in client.calls if c[0] == "get_stock_snapshot"))

    async def test_a_failed_prior_close_fetch_costs_nothing(self):
        """FakeMCPClient raises on an unknown tool: the key is simply absent
        and the snapshot is otherwise identical."""
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        snap = await snapshot.build_snapshot(self._client(), _research_config(), now=now)

        self.assertNotIn("prior_close", snap["options"]["AAPL"])
        self.assertEqual(snap["options"]["AAPL"]["underlying_price"], 200.0)

    async def test_snapshot_is_json_serializable(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        snap = await snapshot.build_snapshot(
            self._client(positions=EMPTY_POSITIONS_RESPONSE), _research_config(), now=now
        )
        json.dumps(snap)  # must not raise

    async def test_open_orders_ride_on_the_account(self):
        """#171: 'checked, none' is [] and 'could not check' is None - the
        prompt and the funnel treat them differently."""
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        client = self._client()
        client.responses["get_orders"] = OPEN_ORDERS_RESPONSE
        snap = await snapshot.build_snapshot(client, _research_config(), now=now)
        self.assertEqual([o["symbol"] for o in snap["account"]["open_orders"]], ["QQQ260903P00709000", "AAPL"])
        self.assertEqual(("get_orders", {"status": "open", "nested": True}), next(c for c in client.calls if c[0] == "get_orders"))

        snap = await snapshot.build_snapshot(self._client(), _research_config(), now=now)
        self.assertEqual(snap["account"]["open_orders"], [])

        def fail(arguments):
            raise RuntimeError("MCP down")

        client = self._client()
        client.responses["get_orders"] = fail
        snap = await snapshot.build_snapshot(client, _research_config(), now=now)
        self.assertIsNone(snap["account"]["open_orders"])

    async def test_empty_account_has_empty_positions_list_not_a_crash(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=EASTERN)
        snap = await snapshot.build_snapshot(
            self._client(positions=EMPTY_POSITIONS_RESPONSE), _research_config(), now=now
        )
        self.assertEqual(snap["account"]["positions"], [])


if __name__ == "__main__":
    unittest.main()

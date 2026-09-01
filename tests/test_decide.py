import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from bot import decide, journal
from bot.models import Proposal


def _config(**overrides):
    config = {
        "underlyings": ["AAPL", "SPY"],
        "max_position_usd": 5000,
        "max_positions": 4,
        "max_contracts_per_order": 10,
        "min_days_to_expiration": 1,
        "max_days_to_expiration": 45,
        "last_entry": "15:15",
        "trade_end": "15:45",
        "research_contracts_per_underlying": 3,
    }
    config.update(overrides)
    return config


def _contract(bid=None, ask=None, last=None):
    raw = {}
    if bid is not None or ask is not None:
        raw["latestQuote"] = {"bp": bid, "ap": ask}
    if last is not None:
        raw["latestTrade"] = {"p": last}
    return raw


TODAY = date(2026, 1, 15)


def _snapshot(**options):
    return {
        "account": {"equity": 100000.0, "cash": 50000.0, "positions": []},
        "options": options,
    }


class SummarizeOptionsTest(unittest.TestCase):
    def test_summarizes_only_the_contracts_that_make_the_menu(self):
        """#158: the fetched chain can be thousands of contracts. The
        Black-Scholes solve inside _summarize_contract must run on the N
        chosen, not the pool - with expired and unparseable symbols dropped
        before the choice so the menu is never left short. The choice itself
        is bot/menu.py's (#159): here one expiry bucket, so the ATM call, the
        ATM put and the out-of-the-money call, in expiry/strike order."""
        snap = _snapshot(
            AAPL={
                "underlying_price": 200.0,
                "contracts": {
                    "AAPL260204C00210000": _contract(bid=1.0, ask=1.2),   # $10 away
                    "AAPL260204C00200000": _contract(bid=5.2, ask=5.5),   # ATM, 20 dte
                    "AAPL260114C00200000": _contract(bid=5.2, ask=5.5),   # ATM but expired
                    "AAPL260227P00200000": _contract(bid=5.0, ask=5.3),   # ATM, 43 dte
                    "AAPL260204P00201000": _contract(bid=5.0, ask=5.3),   # $1 away
                    "not-an-occ-symbol": _contract(bid=1.0, ask=1.1),
                },
            }
        )
        with mock.patch.object(decide, "_summarize_contract", wraps=decide._summarize_contract) as summarize:
            result = decide._summarize_options(snap, _config(research_contracts_per_underlying=3), TODAY)

        chosen = [c["symbol"] for c in result["AAPL"]["contracts"]]
        self.assertEqual(chosen, ["AAPL260204C00200000", "AAPL260204P00201000", "AAPL260204C00210000"])
        self.assertEqual(summarize.call_count, 3)

    def test_derives_strike_type_dte_from_occ_symbol(self):
        snap = _snapshot(
            AAPL={
                "underlying_price": 200.0,
                "contracts": {"AAPL260204C00200000": _contract(bid=5.2, ask=5.5)},
            }
        )
        result = decide._summarize_options(snap, _config(), TODAY)
        contract = result["AAPL"]["contracts"][0]
        self.assertEqual(contract["type"], "call")
        self.assertEqual(contract["strike"], 200.0)
        self.assertEqual(contract["dte"], (date(2026, 2, 4) - TODAY).days)

    def test_spread_pct_is_the_quote_spread_over_the_mid(self):
        snap = _snapshot(
            AAPL={
                "underlying_price": 200.0,
                "contracts": {"AAPL260204C00200000": _contract(bid=4.80, ask=5.20)},
            }
        )
        result = decide._summarize_options(snap, _config(), TODAY)
        contract = result["AAPL"]["contracts"][0]
        self.assertEqual(contract["spread_pct"], 8.0)  # 0.40 / 5.00

    def test_spread_pct_omitted_when_quote_is_one_sided(self):
        snap = _snapshot(
            AAPL={
                "underlying_price": 200.0,
                "contracts": {"AAPL260204C00200000": _contract(ask=5.20, last=5.0)},
            }
        )
        result = decide._summarize_options(snap, _config(), TODAY)
        self.assertNotIn("spread_pct", result["AAPL"]["contracts"][0])

    def test_includes_greeks_when_price_is_usable(self):
        snap = _snapshot(
            AAPL={
                "underlying_price": 200.0,
                "contracts": {"AAPL260204C00200000": _contract(bid=5.2, ask=5.5)},
            }
        )
        result = decide._summarize_options(snap, _config(), TODAY)
        contract = result["AAPL"]["contracts"][0]
        self.assertIn("iv", contract)
        self.assertIn("delta", contract)
        self.assertEqual(contract["greeks_source"], "derived")

    def test_prefers_alpacas_own_greeks_over_the_derivation(self):
        """#160: the snapshot carries impliedVolatility + greeks on most
        contracts, computed on one surface - ours diverged by ~5 delta points
        on NVDA. When present they win, rho comes along, and the source is
        marked so the model knows which numbers are rough."""
        raw = dict(_contract(bid=5.2, ask=5.5), impliedVolatility=0.30601,
                   greeks={"delta": 0.52719, "gamma": 0.037612, "rho": 0.02751, "theta": -0.24512, "vega": 0.13774})
        snap = _snapshot(AAPL={"underlying_price": 200.0, "contracts": {"AAPL260204C00200000": raw}})

        contract = decide._summarize_options(snap, _config(), TODAY)["AAPL"]["contracts"][0]

        self.assertEqual(contract["iv"], 0.306)
        self.assertEqual(contract["delta"], 0.5272)
        self.assertEqual(contract["gamma"], 0.03761)
        self.assertEqual(contract["theta"], -0.2451)
        self.assertEqual(contract["vega"], 0.1377)
        self.assertEqual(contract["rho"], 0.0275)
        self.assertEqual(contract["greeks_source"], "alpaca")

    def test_falls_back_to_the_derivation_when_alpacas_block_is_incomplete(self):
        raw = dict(_contract(bid=5.2, ask=5.5), impliedVolatility=None, greeks={"delta": None})
        snap = _snapshot(AAPL={"underlying_price": 200.0, "contracts": {"AAPL260204C00200000": raw}})

        contract = decide._summarize_options(snap, _config(), TODAY)["AAPL"]["contracts"][0]

        self.assertEqual(contract["greeks_source"], "derived")
        self.assertNotIn("rho", contract)

    def test_alpacas_greeks_do_not_need_a_usable_price(self):
        # A one-sided quote cannot be solved, but Alpaca may still have priced it.
        raw = {"latestQuote": {"bp": 0, "ap": 5.5}, "impliedVolatility": 0.4, "greeks": {"delta": 0.3}}
        snap = _snapshot(AAPL={"underlying_price": 200.0, "contracts": {"AAPL260204C00200000": raw}})

        contract = decide._summarize_options(snap, _config(), TODAY)["AAPL"]["contracts"][0]

        self.assertEqual((contract["iv"], contract["delta"], contract["greeks_source"]), (0.4, 0.3, "alpaca"))

    def test_omits_greeks_when_no_price_data_at_all(self):
        snap = _snapshot(
            AAPL={"underlying_price": 200.0, "contracts": {"AAPL260204C00200000": {}}}
        )
        result = decide._summarize_options(snap, _config(), TODAY)
        contract = result["AAPL"]["contracts"][0]
        self.assertNotIn("iv", contract)
        self.assertNotIn("delta", contract)
        # still present so the model can reason from strike/dte alone
        self.assertEqual(contract["strike"], 200.0)

    def test_omits_greeks_when_iv_unsolvable(self):
        # A price far below intrinsic value has no valid implied vol.
        snap = _snapshot(
            AAPL={
                "underlying_price": 500.0,
                "contracts": {"AAPL260204C00200000": _contract(last=0.01)},
            }
        )
        result = decide._summarize_options(snap, _config(), TODAY)
        contract = result["AAPL"]["contracts"][0]
        self.assertNotIn("iv", contract)

    def test_expired_contract_is_dropped(self):
        snap = _snapshot(
            AAPL={
                "underlying_price": 200.0,
                "contracts": {"AAPL260114C00200000": _contract(bid=5.2, ask=5.5)},  # dte<=0
            }
        )
        result = decide._summarize_options(snap, _config(), TODAY)
        self.assertEqual(result["AAPL"]["contracts"], [])

    def test_trims_to_research_contracts_per_underlying_nearest_the_money(self):
        contracts = {}
        for strike in (180, 190, 200, 210, 220, 230):
            symbol = f"AAPL260204C{int(strike * 1000):08d}"
            contracts[symbol] = _contract(bid=strike * 0.05, ask=strike * 0.06)
        snap = _snapshot(AAPL={"underlying_price": 200.0, "contracts": contracts})

        result = decide._summarize_options(snap, _config(research_contracts_per_underlying=2), TODAY)

        kept = result["AAPL"]["contracts"]
        self.assertEqual(len(kept), 2)
        # 200 is at the money; the second slot is the slightly-OTM call the
        # tactics ask for (#159), not the in-the-money 190.
        self.assertEqual({c["strike"] for c in kept}, {200.0, 210.0})

    def test_underlying_with_no_price_gets_empty_contracts(self):
        snap = _snapshot(AAPL={"underlying_price": None, "contracts": {}})
        result = decide._summarize_options(snap, _config(), TODAY)
        self.assertEqual(result["AAPL"], {"underlying_price": None, "contracts": []})


class BuildPromptTest(unittest.TestCase):
    def test_embeds_config_limits(self):
        snap = _snapshot()
        prompt = decide.build_prompt(snap, _config(), TODAY)
        self.assertIn("$5000", prompt)
        self.assertIn("max 4 concurrent positions", prompt)
        self.assertIn("max 10 option contracts", prompt)
        self.assertIn("AAPL, SPY", prompt)
        self.assertIn("15:15", prompt)
        self.assertIn("15:45", prompt)

    def test_prompt_explains_spread_pct_as_round_trip_cost(self):
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)
        self.assertIn("spread_pct", prompt)
        self.assertIn("round trip", prompt)

    def test_prompt_states_the_rules_that_end_a_position(self):
        """The model was told the expiration WINDOW but not the rules that close
        a position, so it could propose a short-dated contract in good faith
        that the end-of-day backstop would sell hours later - which reads as
        model error in the journal when it is policy the model never saw."""
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)

        self.assertIn("How long a position can actually live", prompt)
        self.assertIn("end-of-day backstop", prompt)

    def test_holding_period_rules_come_from_config_not_hardcoded(self):
        prompt = decide.build_prompt(
            _snapshot(), _config(expiry_close_dte=2, eod_close_dte=6), TODAY
        )

        self.assertIn("once it has 2 day(s) to expiration", prompt)
        self.assertIn("6 day(s) or fewer left", prompt)
        self.assertIn("buy at 6 DTE or nearer", prompt)

    def test_holding_period_defaults_match_the_code_that_enforces_them(self):
        """A config without these keys must still describe what exits.py and
        flatten.py will actually do, or the prompt quietly lies."""
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)

        self.assertIn("once it has 0 day(s) to expiration", prompt)
        self.assertIn("1 day(s) or fewer left", prompt)

    def test_embeds_snapshot_as_json(self):
        snap = _snapshot(
            AAPL={
                "underlying_price": 200.0,
                "contracts": {"AAPL260204C00200000": _contract(bid=5.2, ask=5.5)},
            }
        )
        prompt = decide.build_prompt(snap, _config(), TODAY)
        marker = prompt.index("SNAPSHOT:\n") + len("SNAPSHOT:\n")
        embedded = json.loads(prompt[marker:].rsplit("\n\n", 1)[0])
        self.assertEqual(embedded["account"]["equity"], 100000.0)
        self.assertIn("AAPL", embedded["options"])

    def test_embeds_strategy_notes_verbatim(self):
        prompt = decide.build_prompt(_snapshot(), _config(strategy_notes="THESIS: buy cheap gamma\n"), TODAY)
        self.assertIn("STRATEGY (from config.yaml", prompt)
        self.assertIn("THESIS: buy cheap gamma", prompt)

    def test_missing_strategy_notes_does_not_crash(self):
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)
        self.assertIn("(none)", prompt)

    def test_learning_block_is_embedded_before_the_snapshot_when_given(self):
        block = "RECENT OUTCOMES (facts):\n- 2 closed trades in the window: net +80\n\n"
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY, learning=block)
        self.assertIn("RECENT OUTCOMES", prompt)
        self.assertLess(prompt.index("RECENT OUTCOMES"), prompt.index("SNAPSHOT:"))
        self.assertNotIn("RECENT OUTCOMES", decide.build_prompt(_snapshot(), _config(), TODAY))

    def test_prediction_prior_block_only_when_snapshot_has_it(self):
        snap = _snapshot()
        self.assertNotIn("PREDICTION MARKETS", decide.build_prompt(snap, _config(), TODAY))
        snap["predictions"] = {"SPY": {"series": "KXINX", "close_time": "2026-01-15T21:00:00Z",
                                       "reference_close": 7400.0, "implied_median": 7425.0, "implied_move_pct": 0.34,
                                       "p_above_reference": 0.6, "p_up_over_1pct": 0.2, "p_down_over_1pct": 0.1, "volume": 500}}
        prompt = decide.build_prompt(snap, _config(), TODAY)
        self.assertIn("PREDICTION MARKETS", prompt)
        self.assertIn("SPY via KXINX", prompt)
        self.assertLess(prompt.index("PREDICTION MARKETS"), prompt.index("SNAPSHOT:"))

    def test_positions_block_sits_after_the_prior_and_before_the_snapshot(self):
        """bot/holdings.py's block refers to 'the prior now', so it follows the
        PREDICTION MARKETS block; and like every prose block it precedes the
        JSON so the model reads the rules before the data."""
        block = "POSITIONS YOU HOLD (the ONLY symbols a sell may name - copy them exactly):\n- X x1 @ 1\n\n"
        snap = _snapshot()
        snap["predictions"] = {"SPY": {"series": "KXINX", "close_time": "2026-01-15T21:00:00Z",
                                       "reference_close": 7400.0, "implied_median": 7425.0, "implied_move_pct": 0.34,
                                       "p_above_reference": 0.6, "p_up_over_1pct": 0.2, "p_down_over_1pct": 0.1, "volume": 500}}
        prompt = decide.build_prompt(snap, _config(), TODAY, positions_block=block)
        self.assertLess(prompt.index("PREDICTION MARKETS"), prompt.index("POSITIONS YOU HOLD (the ONLY"))
        self.assertLess(prompt.index("POSITIONS YOU HOLD (the ONLY"), prompt.index("SNAPSHOT:"))
        self.assertNotIn("- X x1 @ 1", decide.build_prompt(snap, _config(), TODAY))

    def test_prompt_explains_the_positions_block_and_resting_orders(self):
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)
        self.assertIn("A sell may name only a symbol from that list, copied exactly", prompt)
        self.assertIn("not by re-deriving a view from scratch", prompt)
        self.assertIn("RESTING ORDERS are buys you already sent that have not filled", prompt)

    def test_requests_only_market_or_limit_orders(self):
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)
        self.assertIn('"market"|"limit"', prompt)


class ExtractJsonArrayTest(unittest.TestCase):
    def test_parses_clean_array(self):
        self.assertEqual(decide._extract_json_array("[]"), [])

    def test_parses_array_wrapped_in_prose_and_fences(self):
        text = 'Here is my decision:\n```json\n[{"action": 1}]\n```\nDone.'
        self.assertEqual(decide._extract_json_array(text), [{"action": 1}])

    def test_raises_when_no_array_present(self):
        with self.assertRaises(ValueError):
            decide._extract_json_array("I choose to hold everything.")


class ParseProposalTest(unittest.TestCase):
    def test_option_action_derives_underlying_from_symbol(self):
        p = decide._parse_proposal(
            {"instrument": "option", "symbol": "aapl260204c00200000", "side": "buy", "qty": 2}
        )
        self.assertEqual(p.instrument, "option")
        self.assertEqual(p.symbol, "AAPL260204C00200000")
        self.assertEqual(p.underlying, "AAPL")
        self.assertEqual(p.qty, 2)

    def test_stock_action_has_no_underlying(self):
        p = decide._parse_proposal({"instrument": "stock", "symbol": "aapl", "side": "sell", "qty": 5})
        self.assertIsNone(p.underlying)
        self.assertEqual(p.symbol, "AAPL")

    def test_malformed_option_symbol_leaves_underlying_none_not_a_crash(self):
        p = decide._parse_proposal({"instrument": "option", "symbol": "not-occ", "side": "buy", "qty": 1})
        self.assertIsNone(p.underlying)

    def test_garbage_qty_defaults_to_zero_not_a_crash(self):
        p = decide._parse_proposal({"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": "lots"})
        self.assertEqual(p.qty, 0)

    def test_missing_qty_defaults_to_zero(self):
        p = decide._parse_proposal({"instrument": "stock", "symbol": "AAPL", "side": "buy"})
        self.assertEqual(p.qty, 0)

    def test_limit_price_parsed_when_present(self):
        p = decide._parse_proposal(
            {"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": 1,
             "order_type": "limit", "limit_price": "199.5"}
        )
        self.assertEqual(p.limit_price, 199.5)

    def test_garbage_limit_price_becomes_none_not_a_crash(self):
        p = decide._parse_proposal(
            {"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": 1, "limit_price": "n/a"}
        )
        self.assertIsNone(p.limit_price)

    def test_order_type_defaults_to_market(self):
        p = decide._parse_proposal({"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": 1})
        self.assertEqual(p.order_type, "market")


class FakeFeatherlessClient:
    def __init__(self, content: str, usage=None, model="fake/model", finish_reason="stop", reasoning=None):
        self.content = content
        self.usage = usage
        self.model = model
        self.finish_reason = finish_reason
        self.reasoning = reasoning
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        message = {"content": self.content}
        if self.reasoning is not None:
            message["reasoning"] = self.reasoning
        response = {"choices": [{"message": message, "finish_reason": self.finish_reason}], "model": self.model}
        if self.usage is not None:
            response["usage"] = self.usage
        return response


class ThinkingModelTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_params_merge_into_request(self):
        client = FakeFeatherlessClient("[]")
        cfg = _config(temperature=0.1, model_params={"chat_template_kwargs": {"enable_thinking": False}})
        await decide.decide(_snapshot(), cfg, client, TODAY)
        _, kwargs = client.calls[0]
        self.assertEqual(kwargs["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(kwargs["temperature"], 0.1)

    async def test_reasoning_and_finish_reason_are_captured_and_truncated(self):
        client = FakeFeatherlessClient("[]", reasoning="x" * 5000)
        d = await decide.decide(_snapshot(), _config(), client, TODAY)
        self.assertEqual(d.finish_reason, "stop")
        self.assertEqual(len(d.reasoning), decide.REASONING_KEEP_CHARS)

    async def test_empty_content_with_length_finish_is_a_clear_truncation_error(self):
        client = FakeFeatherlessClient("", finish_reason="length", reasoning="thinking..." * 100,
                                       usage={"completion_tokens": 800})
        with self.assertRaises(decide.TruncatedOutput) as ctx:
            await decide.decide(_snapshot(), _config(), client, TODAY)
        msg = str(ctx.exception)
        self.assertIn("finish_reason=length", msg)
        self.assertIn("800 completion tokens", msg)
        self.assertIn("model_params", msg)

    async def test_empty_content_with_stop_finish_is_the_ordinary_no_json_error(self):
        client = FakeFeatherlessClient("", finish_reason="stop")
        with self.assertRaises(ValueError) as ctx:
            await decide.decide(_snapshot(), _config(), client, TODAY)
        self.assertNotIsInstance(ctx.exception, decide.TruncatedOutput)

    async def test_error_body_without_choices_surfaces_the_providers_message(self):
        """A 200 carrying an error object instead of choices used to raise a
        bare KeyError: 'choices', which told an operator nothing about the
        real cause (rate limit, spent credits, model unavailable)."""

        class ErrorBodyClient:
            model = "m"

            async def chat(self, messages, **kwargs):
                return {"error": {"message": "insufficient credits", "type": "billing"}}

        with self.assertRaises(RuntimeError) as ctx:
            await decide.decide(_snapshot(), _config(), ErrorBodyClient(), TODAY)
        msg = str(ctx.exception)
        self.assertIn("no choices", msg)
        self.assertIn("insufficient credits", msg)

    async def test_empty_choices_list_is_also_reported_not_an_indexerror(self):
        class EmptyChoicesClient:
            model = "m"

            async def chat(self, messages, **kwargs):
                return {"choices": []}

        with self.assertRaises(RuntimeError):
            await decide.decide(_snapshot(), _config(), EmptyChoicesClient(), TODAY)

    def test_prompt_tells_the_model_not_to_audit_greeks(self):
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)
        self.assertIn("do NOT spend effort auditing", prompt)

    def test_prompt_explains_where_the_greeks_come_from(self):
        prompt = decide.build_prompt(_snapshot(), _config(), TODAY)
        self.assertIn('greeks_source "alpaca"', prompt)
        self.assertIn('greeks_source "derived"', prompt)
        self.assertNotIn("carries no Greeks", prompt)
        self.assertNotIn("not be internally consistent", prompt)


class DecideTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_parsed_proposals_and_raw_text(self):
        raw = '[{"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": 3, "reason": "x"}]'
        client = FakeFeatherlessClient(raw)

        d = await decide.decide(_snapshot(), _config(), client, TODAY)

        self.assertEqual(len(d.proposals), 1)
        self.assertIsInstance(d.proposals[0], Proposal)
        self.assertEqual(d.proposals[0].symbol, "AAPL")
        self.assertEqual(d.raw, raw)

    async def test_carries_model_usage_and_latency(self):
        client = FakeFeatherlessClient("[]", usage={"prompt_tokens": 3000, "completion_tokens": 2, "total_tokens": 3002})
        d = await decide.decide(_snapshot(), _config(), client, TODAY)
        self.assertEqual(d.model, "fake/model")
        self.assertEqual(d.usage["total_tokens"], 3002)
        self.assertGreaterEqual(d.latency_sec, 0.0)

    async def test_missing_usage_is_none_not_a_crash(self):
        d = await decide.decide(_snapshot(), _config(), FakeFeatherlessClient("[]"), TODAY)
        self.assertIsNone(d.usage)

    async def test_passes_temperature_and_max_tokens_from_config(self):
        client = FakeFeatherlessClient("[]")
        await decide.decide(_snapshot(), _config(temperature=0.35, max_tokens=500), client, TODAY)
        _, kwargs = client.calls[0]
        self.assertEqual(kwargs, {"temperature": 0.35, "max_tokens": 500})

    async def test_omits_sampling_kwargs_when_config_lacks_them(self):
        client = FakeFeatherlessClient("[]")
        await decide.decide(_snapshot(), _config(), client, TODAY)
        _, kwargs = client.calls[0]
        self.assertEqual(kwargs, {})

    async def test_empty_array_means_no_proposals(self):
        d = await decide.decide(_snapshot(), _config(), FakeFeatherlessClient("[]"), TODAY)
        self.assertEqual(d.proposals, [])

    async def test_sends_prompt_as_single_user_message(self):
        client = FakeFeatherlessClient("[]")
        await decide.decide(_snapshot(), _config(), client, TODAY)

        messages, _ = client.calls[0]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("SNAPSHOT:", messages[0]["content"])

    async def test_non_dict_entries_in_array_are_skipped_not_a_crash(self):
        client = FakeFeatherlessClient('["hold", {"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": 1}]')
        d = await decide.decide(_snapshot(), _config(), client, TODAY)
        self.assertEqual(len(d.proposals), 1)


class ScriptedClient:
    """Returns canned responses in order; records every request's kwargs."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.model = "fake/model"

    async def chat(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        r = self.responses.pop(0)
        return r


def _tool_call_response(name, args, call_id="c1"):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def _answer(content="[]"):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 5}, "model": "fake/model"}


class FakeContentBlock:
    def __init__(self, text):
        self.text = text


class FakeMCP:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        r = type("R", (), {})()
        r.content = [FakeContentBlock(json.dumps({"data": {"bars": {"SPY": []}}}))]
        return r


class ResearchLoopTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = journal.JOURNAL
        journal.JOURNAL = Path(self.tmp.name) / "journal.jsonl"
        self.addCleanup(lambda: setattr(journal, "JOURNAL", self._orig))
        self.journal_path = journal.JOURNAL

    def _cfg(self, **o):
        return _config(research_tools_enabled=True, research_max_tool_calls=2, **o)

    async def test_disabled_by_default_makes_a_single_call_without_tools(self):
        client = ScriptedClient([_answer()])
        d = await decide.decide(_snapshot(), _config(), client, TODAY, mcp=FakeMCP())
        self.assertEqual(d.tool_calls, [])
        self.assertNotIn("tools", client.calls[0][1])

    async def test_no_mcp_client_means_no_tools_even_if_enabled(self):
        client = ScriptedClient([_answer()])
        await decide.decide(_snapshot(), self._cfg(), client, TODAY, mcp=None)
        self.assertNotIn("tools", client.calls[0][1])

    async def test_tool_call_is_executed_fed_back_and_journaled(self):
        client = ScriptedClient([_tool_call_response("get_bars", {"symbol": "SPY", "timeframe": "15Min"}), _answer("[]")])
        mcp = FakeMCP()
        d = await decide.decide(_snapshot(), self._cfg(), client, TODAY, mcp=mcp)

        self.assertEqual(mcp.calls[0][0], "get_stock_bars")
        self.assertEqual(mcp.calls[0][1]["symbols"], "SPY")
        self.assertEqual([t["name"] for t in d.tool_calls], ["get_bars"])
        # second request carries the assistant tool_calls + the tool result
        messages, kwargs = client.calls[1]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["tool_call_id"], "c1")
        self.assertIn("tools", kwargs)
        # usage summed across both requests
        self.assertEqual(d.usage["prompt_tokens"], 300)
        journaled = [json.loads(line) for line in self.journal_path.read_text().splitlines()]
        self.assertEqual([r["event"] for r in journaled], ["tool_call"])
        self.assertEqual(journaled[0]["tool"], "get_bars")

    async def test_budget_is_enforced_then_model_must_answer(self):
        client = ScriptedClient([
            _tool_call_response("get_news", {"symbols": "SPY"}, "c1"),
            _tool_call_response("get_news", {"symbols": "QQQ"}, "c2"),
            _answer("[]"),  # the final request is made without tools, so the model can only answer
        ])
        d = await decide.decide(_snapshot(), self._cfg(), client, TODAY, mcp=FakeMCP())
        self.assertEqual(len(d.tool_calls), 2)
        self.assertEqual(len(client.calls), 3)
        # after the budget is spent the final request is made WITHOUT tools
        self.assertNotIn("tools", client.calls[-1][1])
        self.assertIn("Research budget used", client.calls[-1][0][-1]["content"])

    async def test_prompt_mentions_tools_only_when_enabled(self):
        self.assertIn("RESEARCH TOOLS", decide.build_prompt(_snapshot(), self._cfg(), TODAY, tools=True))
        self.assertNotIn("RESEARCH TOOLS", decide.build_prompt(_snapshot(), self._cfg(), TODAY, tools=False))


if __name__ == "__main__":
    unittest.main()

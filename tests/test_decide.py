import json
import unittest
from datetime import date

from bot import decide
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
        # 200 is exactly at the money; 190 ties 210 on distance but sorts
        # first (stable sort preserves the original dict's insertion order).
        self.assertEqual({c["strike"] for c in kept}, {200.0, 190.0})

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
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {"choices": [{"message": {"content": self.content}}]}


class DecideTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_parsed_proposals_and_raw_text(self):
        raw = '[{"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": 3, "reason": "x"}]'
        client = FakeFeatherlessClient(raw)

        proposals, returned_raw = await decide.decide(_snapshot(), _config(), client, TODAY)

        self.assertEqual(len(proposals), 1)
        self.assertIsInstance(proposals[0], Proposal)
        self.assertEqual(proposals[0].symbol, "AAPL")
        self.assertEqual(returned_raw, raw)

    async def test_empty_array_means_no_proposals(self):
        client = FakeFeatherlessClient("[]")
        proposals, _ = await decide.decide(_snapshot(), _config(), client, TODAY)
        self.assertEqual(proposals, [])

    async def test_sends_prompt_as_single_user_message(self):
        client = FakeFeatherlessClient("[]")
        await decide.decide(_snapshot(), _config(), client, TODAY)

        messages, _ = client.calls[0]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("SNAPSHOT:", messages[0]["content"])

    async def test_non_dict_entries_in_array_are_skipped_not_a_crash(self):
        client = FakeFeatherlessClient('["hold", {"instrument": "stock", "symbol": "AAPL", "side": "buy", "qty": 1}]')
        proposals, _ = await decide.decide(_snapshot(), _config(), client, TODAY)
        self.assertEqual(len(proposals), 1)


if __name__ == "__main__":
    unittest.main()

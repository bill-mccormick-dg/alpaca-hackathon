"""Which model writes the end-of-day critique.

docs/strategy.md promised "no account is ever its own reviewer" from #127 on,
and bot/config.py::resolve_review_model() delivered the choice - to the
journal and the dashboard, never to the review call itself, which stayed on
config["model"] (#177). These pin the call, not the function.
"""

import unittest
from typing import ClassVar
from unittest.mock import patch

import eod_review

CONFIG = {
    "model": "moonshotai/Kimi-K2.6",
    "review_model_preference": ["moonshotai/Kimi-K2.6", "moonshotai/Kimi-K2-Instruct"],
    "strategy_notes": "THESIS: buy premium.\nTactics: ...",
}


class CapturingClient:
    """Stands in for FeatherlessClient: remembers the model it was built
    with and answers any chat with a fixed critique."""

    built_with: ClassVar[list[str]] = []
    last_prompt: ClassVar[str] = ""

    def __init__(self, api_key, model, timeout=60):
        CapturingClient.built_with.append(model)
        self.model = model

    async def chat(self, messages, **kwargs):
        CapturingClient.last_prompt = messages[0]["content"]
        return {"choices": [{"message": {"content": "  hold everything.  "}}]}


class ReviewerModelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        CapturingClient.built_with = []

    async def test_the_review_is_written_by_a_model_that_did_not_trade(self):
        with patch.object(eod_review, "FeatherlessClient", CapturingClient):
            text, model = await eod_review.model_recommendation(CONFIG, {"FEATHERLESS_API_KEY": "k"}, {"trips": []})
        self.assertEqual(CapturingClient.built_with, ["moonshotai/Kimi-K2-Instruct"])
        self.assertEqual(model, "moonshotai/Kimi-K2-Instruct")
        self.assertEqual(text, "hold everything.")

    async def test_an_explicit_review_model_wins(self):
        config = dict(CONFIG, review_model="Qwen/Qwen3.8-Flash-Next")
        with patch.object(eod_review, "FeatherlessClient", CapturingClient):
            _, model = await eod_review.model_recommendation(config, {"FEATHERLESS_API_KEY": "k"}, {})
        self.assertEqual(model, "Qwen/Qwen3.8-Flash-Next")

    def test_with_nothing_else_to_pick_the_trading_model_reviews_itself(self):
        """A same-model review beats no review - but only as the fallback."""
        self.assertEqual(eod_review.reviewer_model({"model": "x/only"}), "x/only")
        self.assertEqual(eod_review.reviewer_model({}), eod_review.DEFAULT_MODEL)

    async def test_a_reviewer_that_traded_under_an_expired_override_is_not_asked(self):
        """#218: the config says K3 traded and pins Qwen; the journal says
        Qwen made every decision. The reviewer is chosen against the journal."""
        config = {"model": "moonshotai/Kimi-K3", "review_model": "Qwen/Qwen3.8-Flash-Next",
                  "review_model_preference": ["moonshotai/Kimi-K2.6", "Qwen/Qwen3.8-Flash-Next"]}
        digest = {"audit": {"by_model": {"Qwen/Qwen3.8-Flash-Next": {"decisions": 29, "errors": 0},
                                         "moonshotai/Kimi-K3": {"decisions": 0, "errors": 6},
                                         "(no model)": {"decisions": 0, "errors": 1}}}}

        self.assertEqual(eod_review.traded_models(digest), {"Qwen/Qwen3.8-Flash-Next", "moonshotai/Kimi-K3"})
        self.assertEqual(eod_review.reviewer_model(config, eod_review.traded_models(digest)), "moonshotai/Kimi-K2.6")
        with patch.object(eod_review, "FeatherlessClient", CapturingClient):
            _, model = await eod_review.model_recommendation(config, {"FEATHERLESS_API_KEY": "k"}, digest)
        self.assertEqual(model, "moonshotai/Kimi-K2.6")
        self.assertIn("Qwen/Qwen3.8-Flash-Next (29 decisions), moonshotai/Kimi-K3 (6 errors)", CapturingClient.last_prompt)

    def test_an_explicit_pin_equal_to_the_trading_model_falls_through(self):
        config = {"model": "x/a", "review_model": "x/a", "review_model_preference": ["x/a", "x/b"]}
        self.assertEqual(eod_review.reviewer_model(config), "x/b")

    async def test_the_prompt_names_the_trading_model_and_not_as_the_reader(self):
        """The old prompt opened with 'the decisions below were yours' - true
        only while the reviewer was the trader, which was the bug."""
        with patch.object(eod_review, "FeatherlessClient", CapturingClient):
            await eod_review.model_recommendation(CONFIG, {"FEATHERLESS_API_KEY": "k"}, {})
        prompt = CapturingClient.last_prompt
        self.assertIn("made by a different model, moonshotai/Kimi-K2.6", prompt)
        self.assertNotIn("were yours", prompt)


if __name__ == "__main__":
    unittest.main()

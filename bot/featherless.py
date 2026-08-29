"""Thin async client for Featherless.ai's OpenAI-compatible chat API.

Featherless replaces the Claude CLI alpaca-trader uses for its decision
step. This is deliberately minimal — a working chat-completion call, proven
against the real API — not the agentic tool-calling loop (that's a later
step, once the risk/execute guardrails exist to gate what a model-proposed
order is allowed to do; see README.md "Architecture").
"""

import httpx

BASE_URL = "https://api.featherless.ai/v1"

# Featherless docs confirm native function/tool-calling support on this
# model (also: Qwen 3 family). Highlighted in Featherless's own hackathon
# setup materials as strong at agentic tool use — a fit for the decision
# loop this client will eventually drive.
DEFAULT_MODEL = "moonshotai/Kimi-K2-Instruct"


class FeatherlessClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    async def chat(self, messages: list[dict], **kwargs) -> dict:
        """POST /chat/completions. Extra kwargs (tools, tool_choice,
        temperature, ...) pass straight through to the request body."""
        async with httpx.AsyncClient(
            base_url=BASE_URL, transport=self._transport, timeout=self.timeout
        ) as client:
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "messages": messages, **kwargs},
            )
            response.raise_for_status()
            return response.json()

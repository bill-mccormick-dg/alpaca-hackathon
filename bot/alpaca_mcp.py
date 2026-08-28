"""Thin async client for Alpaca's official MCP server.

Launches the `alpaca-mcp-server` console script (from requirements.txt — a
real pip-installed entry point, no `uv`/`uvx` dependency) as a stdio
subprocess and speaks MCP to it via the `mcp` package's client session.

Step 1 scope: read-only connectivity. Order-placing tools exist on the
server (`place_stock_order`, `place_option_order`, ...) but nothing here
calls them yet — a later step will route all writes through our own
risk-check first, mirroring alpaca-trader's risk.py/execute.py funnel, so
the model never gets a direct path to submit an order.
"""

import os
import shutil
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Self

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _resolve_server_command() -> str:
    """Find the alpaca-mcp-server console script.

    Resolved relative to the *running* interpreter (sys.executable) rather
    than a bare PATH lookup — `./.venv/bin/python script.py` without
    activating the venv leaves .venv/bin off PATH, but sys.executable still
    points at .venv/bin/python, so its sibling console script is reliable
    either way. Falls back to PATH for a globally-installed server.
    """
    sibling = Path(sys.executable).with_name("alpaca-mcp-server")
    if sibling.exists():
        return str(sibling)
    found = shutil.which("alpaca-mcp-server")
    if found:
        return found
    raise RuntimeError(
        "alpaca-mcp-server not found next to the current Python interpreter "
        f"({sys.executable}) or on PATH. Is it installed (pip install -r requirements.txt)?"
    )


class AlpacaMCPClient:
    """Async context manager around a running alpaca-mcp-server process."""

    def __init__(self, api_key: str, secret_key: str):
        self._api_key = api_key
        self._secret_key = secret_key
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        params = StdioServerParameters(
            command=_resolve_server_command(),
            args=[],
            env={
                **os.environ,
                "ALPACA_API_KEY": self._api_key,
                "ALPACA_SECRET_KEY": self._secret_key,
                # Hardcoded, not configurable: this project has no live-
                # trading code path, matching alpaca-trader's paper=True.
                "ALPACA_PAPER_TRADE": "true",
            },
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._stack.aclose()

    async def list_tools(self):
        result = await self.session.list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict | None = None):
        return await self.session.call_tool(name, arguments or {})

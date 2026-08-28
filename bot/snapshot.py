"""Builds the state the decision loop and risk checks consume each cycle.

This module has two halves, added incrementally: account/position state
(deterministic — "what do we currently hold") and market/options research
("what's out there" — added in a later step). Response shapes here are
Alpaca's own REST objects, which the MCP server proxies close to 1:1
(confirmed against live get_account_info/get_all_positions output) —
fields follow https://docs.alpaca.markets/reference/getaccount and
https://docs.alpaca.markets/reference/getallopenpositions.
"""

import json

from bot.alpaca_mcp import AlpacaMCPClient
from bot.models import AccountState, Position
from bot.occ import parse_occ_symbol


def _data(result) -> dict:
    """Unwrap an MCP tool_call result's {"data": ...} envelope."""
    text = "\n".join(block.text for block in getattr(result, "content", []) if hasattr(block, "text"))
    payload = json.loads(text)
    return payload.get("data", payload) if isinstance(payload, dict) else payload


async def build_positions(client: AlpacaMCPClient) -> dict:
    result = await client.call_tool("get_all_positions")
    data = _data(result)
    raw_positions = data.get("result", data) if isinstance(data, dict) else data

    positions = {}
    for raw in raw_positions:
        symbol = raw["symbol"]
        is_option = raw.get("asset_class") == "us_option"
        underlying = None
        if is_option:
            try:
                underlying = parse_occ_symbol(symbol).underlying
            except ValueError:
                underlying = None  # unexpected symbol shape; leave for risk.py to reject downstream

        # abs(): this project is long-only (no proposal path ever opens a
        # short — see risk.py's sell check, which rejects selling more than
        # is held starting from a flat position). Position qty/value are
        # treated as plain magnitudes throughout the codebase.
        positions[symbol] = Position(
            symbol=symbol,
            instrument="option" if is_option else "stock",
            qty=abs(float(raw["qty"])),
            market_value=abs(float(raw.get("market_value", 0))),
            underlying=underlying,
        )
    return positions


async def build_account_state(client: AlpacaMCPClient) -> AccountState:
    result = await client.call_tool("get_account_info")
    data = _data(result)
    return AccountState(
        equity=float(data["equity"]),
        # Alpaca's last_equity = equity as of the prior trading day's close
        # — exactly today's start-of-day equity.
        start_of_day_equity=float(data["last_equity"]),
        cash=float(data["cash"]),
        positions=await build_positions(client),
    )

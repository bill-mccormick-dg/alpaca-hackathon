#!/usr/bin/env python3
"""Manual connectivity check — not part of the automated test suite.

Confirms we can authenticate to the real Alpaca paper account through
Alpaca's official MCP server and read account state back. Mirrors
alpaca-trader's own precedent of a manual "Test:" command (its README:
`run_cycle.py --dry-run --force`) rather than a live-hitting CI test.

Usage: python scripts/verify_connection.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.alpaca_mcp import AlpacaMCPClient
from bot.credentials import load_credentials


def _text(result) -> str:
    parts = [block.text for block in result.content if hasattr(block, "text")]
    return "\n".join(parts)


async def main() -> int:
    creds = load_credentials()
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        account = await client.call_tool("get_account_info")
        clock = await client.call_tool("get_clock")

    print("=== Account ===")
    print(_text(account))
    print("\n=== Market clock ===")
    print(_text(clock))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""Manual connectivity check — not part of the automated test suite.

Confirms we can authenticate to an Alpaca paper account through Alpaca's
official MCP server and read account state back. Mirrors alpaca-trader's
own precedent of a manual "Test:" command (its README:
`run_cycle.py --dry-run --force`) rather than a live-hitting CI test.

Defaults to the TEST account — safe to run any time. Pass --account
official to check the judging account instead (read-only tools only; see
README.md "Account" for why nothing should place an order on it before
Mon Aug 31 9:30 AM ET).

Usage: python scripts/verify_connection.py [--account test|official]
"""

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", choices=("test", "official"), default="test")
    args = parser.parse_args()

    creds = load_credentials(args.account)
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        account = await client.call_tool("get_account_info")
        clock = await client.call_tool("get_clock")

    print(f"=== Account ({args.account}) ===")
    print(_text(account))
    print("\n=== Market clock ===")
    print(_text(clock))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

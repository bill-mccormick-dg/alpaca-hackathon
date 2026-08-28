#!/usr/bin/env python3
"""Manual snapshot check — not part of the automated test suite.

Prints the full assembled snapshot (clock, account, positions, option
research) for the test account. Mirrors verify_connection.py's role for
Step 1.

Usage: python scripts/verify_snapshot.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.alpaca_mcp import AlpacaMCPClient
from bot.config import load_config
from bot.credentials import load_credentials
from bot.snapshot import build_snapshot


async def main() -> int:
    creds = load_credentials("test")
    config = load_config()
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        snap = await build_snapshot(client, config)

    print(json.dumps(snap, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

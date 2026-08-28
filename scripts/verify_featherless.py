#!/usr/bin/env python3
"""Manual connectivity check for Featherless — not part of the automated
test suite. Mirrors verify_connection.py's role for Alpaca.

Usage: python scripts/verify_featherless.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.credentials import load_credentials
from bot.featherless import FeatherlessClient


async def main() -> int:
    # Featherless key is identical on both accounts' credentials files, so
    # "test" vs "official" makes no difference here.
    creds = load_credentials("test")
    client = FeatherlessClient(creds["FEATHERLESS_API_KEY"])

    result = await client.chat(
        [{"role": "user", "content": "Reply with exactly: connection ok"}],
        max_tokens=20,
    )

    message = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    print(f"Model:   {client.model}")
    print(f"Reply:   {message!r}")
    print(f"Usage:   {usage}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""Live check: does the option menu reach the configured DTE window? (#158)

Per whitelisted underlying, fetches the chain exactly as a cycle does and
prints pages fetched, contract count, a histogram of distinct DTEs, whether
the page cap truncated it, and the contracts the model would actually be
shown. Read-only, test account by default - the same convention as the other
scripts/verify_*.py.

    python scripts/verify_option_chain.py
    python scripts/verify_option_chain.py --account official --config config.yaml
"""

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import decide, snapshot
from bot.alpaca_mcp import AlpacaMCPClient
from bot.config import load_config
from bot.credentials import load_credentials, validate_account
from bot.occ import parse_occ_symbol
from bot.risk import EASTERN


async def main(args: argparse.Namespace) -> int:
    validate_account(args.account)
    config = load_config(args.config)
    creds = load_credentials(args.account)
    today = datetime.now(EASTERN).date()
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        research = await snapshot.build_option_research(client, config, today=today)

    menu = decide._summarize_options({"options": research}, config, today)
    window = f"{config['min_days_to_expiration']}-{config['max_days_to_expiration']}"
    print(f"window {window} DTE, band +/-{config['option_strike_band_pct']:.0%}, "
          f"page limit {snapshot.CHAIN_PAGE_LIMIT}, cap {snapshot.CHAIN_MAX_PAGES} pages\n")
    for underlying, block in research.items():
        contracts = block["contracts"]
        dtes = Counter()
        for symbol in contracts:
            try:
                dtes[(parse_occ_symbol(symbol).expiration - today).days] += 1
            except ValueError:
                continue
        flag = "  ** TRUNCATED **" if block["truncated"] else ""
        print(f"{underlying}: spot {block['underlying_price']}  pages {block['pages']}  "
              f"contracts {len(contracts)}  max DTE {block['max_dte']}{flag}")
        print("   DTE histogram: " + ", ".join(f"{d}:{n}" for d, n in sorted(dtes.items())))
        print("   menu:")
        for c in menu[underlying]["contracts"]:
            print(f"     {c['type']:>4} K={c['strike']:<8} dte={c['dte']:<3} "
                  f"bid={c.get('bid')} ask={c.get('ask')} spread%={c.get('spread_pct')}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="test")
    ap.add_argument("--config", default=None)
    sys.exit(asyncio.run(main(ap.parse_args())))

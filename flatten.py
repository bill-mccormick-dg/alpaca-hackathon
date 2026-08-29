#!/usr/bin/env python3
"""Close all positions and cancel all orders, verified against the broker.

Usage:
  flatten.py                     end-of-day flatten (cron backstop; trading resumes tomorrow)
  flatten.py --halt              kill switch: flatten AND create logs/HALT so no cycle runs
                                 again until you delete that file
  flatten.py --account official  the judging account (refused before the official window
                                 opens, same as run_cycle.py - there is nothing to flatten)
"""

import argparse
import asyncio
import sys

from bot import journal
from bot.alpaca_mcp import AlpacaMCPClient
from bot.config import load_config
from bot.credentials import load_credentials
from bot.flatten import flatten_all
from bot.orders import INCOMPLETE
from bot.risk import RiskManager
from run_cycle import OFFICIAL_TRADING_STARTS, official_account_may_trade


async def run(args: argparse.Namespace) -> int:
    if args.account == "official" and not official_account_may_trade():
        print(
            f"refusing: official account has nothing to flatten before "
            f"{OFFICIAL_TRADING_STARTS:%Y-%m-%d %H:%M %Z}",
            file=sys.stderr,
        )
        return 2

    risk = RiskManager(load_config())
    creds = load_credentials(args.account)
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        outcome = await flatten_all(client, verify_timeout_sec=args.verify_timeout)

    journal.log(
        "flatten",
        account=args.account,
        halt=args.halt,
        **{k: v for k, v in vars(outcome).items() if k != "extra"},
    )

    if args.halt:
        halt = risk.manual_halt_file()
        halt.parent.mkdir(exist_ok=True)
        halt.write_text("manual kill switch\n")
        journal.log("manual_halt", account=args.account)
        print(f"HALT file created: {halt} - delete it to resume trading")

    if not outcome.cancels_settled:
        print("WARNING: order cancellations did not settle before closing", file=sys.stderr)
    if outcome.failed:
        print(
            f"close FAILED for {[f['symbol'] for f in outcome.failed]}: {outcome.failed[0].get('body')}",
            file=sys.stderr,
        )
    print(outcome.message)
    return 1 if outcome.state == INCOMPLETE else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--halt", action="store_true")
    ap.add_argument("--account", choices=("test", "official"), default="test")
    ap.add_argument("--verify-timeout", type=float, default=30.0, help="seconds to wait for closes to fill")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

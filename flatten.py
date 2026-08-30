#!/usr/bin/env python3
"""Close all positions and cancel all orders, verified against the broker.

Usage:
  flatten.py                     close EVERYTHING (manual, or the final-day backstop)
  flatten.py --expiring-only     end-of-day backstop (cron): close only option contracts
                                 expiring within config eod_close_dte days; hold the rest
                                 overnight. Ignored on/after config final_flatten_date,
                                 when everything is closed.
  flatten.py --halt              kill switch: flatten this account AND halt it (writes
                                 logs/HALT_manual[_<account>]) until you delete that file
  flatten.py --halt --all-accounts
                                 break-glass: same, but writes logs/HALT, which halts
                                 EVERY account. CLI-only on purpose - the Home Assistant
                                 buttons can only ever halt their own account, so a
                                 dashboard tap can't stop the judging account by mistake
  flatten.py --account official  the judging account (refused before the official window
                                 opens, same as run_cycle.py - there is nothing to flatten)
"""

import argparse
import asyncio
import sys
from datetime import date, datetime

from bot import journal, mqtt, overrides
from bot.alpaca_mcp import AlpacaMCPClient
from bot.config import load_config
from bot.credentials import load_credentials, validate_account
from bot.flatten import flatten_all, flatten_expiring
from bot.orders import INCOMPLETE
from bot.risk import EASTERN, RiskManager
from run_cycle import OFFICIAL_TRADING_STARTS, official_account_may_trade


async def run(args: argparse.Namespace) -> int:
    if args.account == "official" and not official_account_may_trade():
        print(
            f"refusing: official account has nothing to flatten before "
            f"{OFFICIAL_TRADING_STARTS:%Y-%m-%d %H:%M %Z}",
            file=sys.stderr,
        )
        return 2

    validate_account(args.account)
    journal.use_account(args.account)
    overrides.use_account(args.account)
    config = load_config(args.config)
    mqtt.configure(config, args.account)
    risk = RiskManager(config, account=args.account)
    today = datetime.now(EASTERN).date()
    final_day = date.fromisoformat(str(config["final_flatten_date"])) if config.get("final_flatten_date") else None
    expiring_only = args.expiring_only and not (final_day and today >= final_day)
    if args.expiring_only and not expiring_only:
        print(f"final flatten date {final_day} reached - flattening everything, not just expiring contracts")

    creds = load_credentials(args.account)
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        if expiring_only:
            outcome = await flatten_expiring(
                client, today, int(config.get("eod_close_dte", 1)), verify_timeout_sec=args.verify_timeout
            )
        else:
            outcome = await flatten_all(client, verify_timeout_sec=args.verify_timeout)

    journal.log(
        "flatten",
        account=args.account,
        halt=args.halt,
        expiring_only=expiring_only,
        **{k: v for k, v in vars(outcome).items() if k != "extra"},
    )

    if args.halt:
        halt = risk.global_halt_file() if args.all_accounts else risk.manual_halt_file()
        scope = "ALL accounts" if args.all_accounts else args.account
        halt.parent.mkdir(exist_ok=True)
        halt.write_text(f"manual kill switch ({scope})\n")
        journal.log("manual_halt", account=args.account, all_accounts=args.all_accounts)
        print(f"HALT file created: {halt} - halts {scope}; delete it to resume trading")

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
    ap.add_argument(
        "--all-accounts",
        action="store_true",
        help="with --halt: write the GLOBAL halt file (logs/HALT), stopping every account, "
        "not just --account. Break-glass only; the HA kill-switch buttons never do this.",
    )
    ap.add_argument("--expiring-only", action="store_true", help="close only contracts expiring within eod_close_dte days")
    ap.add_argument("--account", default="test", help="named account: official, test, or any credentials-<name>.env")
    ap.add_argument("--config", default=None, help="config file (default config.yaml)")
    ap.add_argument("--verify-timeout", type=float, default=30.0, help="seconds to wait for closes to fill")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

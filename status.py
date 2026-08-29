#!/usr/bin/env python3
"""Quick status: halt state, account, positions, today's journal summary.

Read-only - safe to run against the official account at any time.

Usage: status.py [--account test|official] [--json]
"""

import argparse
import asyncio
import json
import sys

from bot import journal
from bot.alpaca_mcp import AlpacaMCPClient
from bot.config import load_config
from bot.credentials import load_credentials
from bot.risk import RiskManager
from bot.snapshot import _data


def halt_state(risk: RiskManager) -> dict:
    return {
        "manual_halt": risk.manual_halt_file().exists(),
        "daily_halt": risk.daily_halt_file().exists(),
    }


def format_positions(raw_positions: list) -> list[dict]:
    """Trim Alpaca's position objects to what an operator wants to see."""
    out = []
    for p in raw_positions:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "symbol": p.get("symbol"),
                "asset_class": p.get("asset_class"),
                "qty": _num(p.get("qty")),
                "avg_entry_price": _num(p.get("avg_entry_price")),
                "market_value": _num(p.get("market_value")),
                "unrealized_pl": _num(p.get("unrealized_pl")),
            }
        )
    return out


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def broker_status(account: str) -> dict:
    creds = load_credentials(account)
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        acct = _data(await client.call_tool("get_account_info"))
        positions = _data(await client.call_tool("get_all_positions"))
    raw_positions = positions.get("result", positions) if isinstance(positions, dict) else positions
    equity, last = _num(acct.get("equity")), _num(acct.get("last_equity"))
    return {
        "account_number": acct.get("account_number"),
        "equity": equity,
        "cash": _num(acct.get("cash")),
        "day_pnl": (equity - last) if equity is not None and last is not None else None,
        "positions": format_positions(raw_positions or []),
    }


async def run(args: argparse.Namespace) -> int:
    config = load_config()
    risk = RiskManager(config)
    report = {"account": args.account, "halt": halt_state(risk), "overrides": config.get("_overrides", {})}
    try:
        report["broker"] = await broker_status(args.account)
    except Exception as exc:  # noqa: BLE001 - status must still print halt state + journal
        report["broker_error"] = str(exc)
    report["today"] = journal.daily_summary()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    h = report["halt"]
    print("== Halt state ==")
    print(f"  manual HALT: {'YES' if h['manual_halt'] else 'no'}")
    print(f"  daily halt:  {'YES' if h['daily_halt'] else 'no'}")

    ov = report["overrides"]
    print(f"\n== Runtime overrides ({len(ov)}) ==")
    for key, entry in sorted(ov.items()):
        value = entry["value"]
        shown = (value.strip().splitlines()[0] + " ...") if isinstance(value, str) and "\n" in value else value
        print(f"  {key} = {shown!r}  until {entry.get('until')}  ({entry.get('set_by')})")
    if not ov:
        print("  (none - pure config.yaml)")

    b = report.get("broker")
    print(f"\n== Account ({args.account}, paper) ==")
    if b is None:
        print(f"  (broker unavailable: {report['broker_error']})")
    else:
        pnl = f"{b['day_pnl']:+,.2f}" if b["day_pnl"] is not None else "-"
        print(f"  {b['account_number']}  equity ${b['equity']:,.2f}  cash ${b['cash']:,.2f}  day P&L ${pnl}")
        print("\n== Positions ==")
        if not b["positions"]:
            print("  (none)")
        for p in b["positions"]:
            upl = f"{p['unrealized_pl']:+,.2f}" if p["unrealized_pl"] is not None else "-"
            print(
                f"  {p['symbol']}: {p['qty']:g} @ ${p['avg_entry_price'] or 0:.2f}  "
                f"value ${p['market_value'] or 0:,.2f}  P&L ${upl}"
            )

    t = report["today"]
    print(f"\n== Today ({t['date']}) ==")
    print(
        f"  cycles {t['cycles']}  orders {t['orders']}  rejected {t['rejected']}  "
        f"errors {t['errors']}  halts {t['halts'] or '-'}"
    )
    for tr in t["trades"]:
        print(f"  {tr['ts']}  {tr['side']} {tr['qty']} {tr['symbol']}: {tr['reason']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", choices=("test", "official"), default="test")
    ap.add_argument("--json", action="store_true")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

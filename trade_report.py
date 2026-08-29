#!/usr/bin/env python3
"""Reconstruct completed round trips from Alpaca's order history.

The journal knows what the bot decided; the broker knows how it ended.
This pairs the two: fills come from Alpaca (via the MCP server), the
journal supplies provenance (which sells were a stop / take-profit /
expiry rule, which were the model's idea, which were the EOD flatten).

Usage:
  trade_report.py                     last 7 days, test account
  trade_report.py --days 1            today only
  trade_report.py --account official  the judging account (read-only, safe any time)
  trade_report.py --json              machine-readable, for eod_review and analysis
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from bot import journal, overrides
from bot.alpaca_mcp import AlpacaMCPClient
from bot.credentials import load_credentials, validate_account
from bot.snapshot import _data
from bot.trades import EXIT_REASONS, classify_exit, pair_round_trips, summarize

REASON_LABEL = {
    "stop_loss": "stop-loss",
    "take_profit": "take-profit",
    "expiry": "expiry close",
    "model": "model sell",
    "flatten": "EOD flatten",
}


def journaled_sells() -> dict:
    """order_id and client_order_id -> reason, for every sell the bot
    itself submitted (the journal is the only source of that intent)."""
    out = {}
    for r in journal.read_events("all", events=("order_submitted",)):
        if r.get("side") != "sell":
            continue
        reason = r.get("reason") or ""
        if r.get("order_id"):
            out[str(r["order_id"])] = reason
        if r.get("client_order_id"):
            out[str(r["client_order_id"])] = reason
    return out


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fills_from_orders(orders: list, sells: dict) -> list[dict]:
    """Flatten Alpaca's (possibly nested) order list into filled buy/sell
    events. Legs are walked so multi-leg/bracket fills aren't missed."""
    fills = []

    def walk(o: dict):
        qty = float(o.get("filled_qty") or 0)
        filled_at = _dt(o.get("filled_at"))
        price = o.get("filled_avg_price")
        if qty > 0 and filled_at and price:
            side = str(o.get("side", "")).lower()
            fills.append(
                {
                    "symbol": o.get("symbol"),
                    "side": side,
                    "qty": qty,
                    "price": float(price),
                    "filled_at": filled_at,
                    "reason": classify_exit(str(o.get("id")), o.get("client_order_id"), sells) if side == "sell" else None,
                }
            )
        for leg in o.get("legs") or []:
            if isinstance(leg, dict):
                walk(leg)

    for o in orders:
        if isinstance(o, dict):
            walk(o)
    return fills


async def fetch_orders(account: str, days: int) -> list:
    creds = load_credentials(account)
    after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with AlpacaMCPClient(creds["ALPACA_API_KEY"], creds["ALPACA_SECRET_KEY"]) as client:
        data = _data(await client.call_tool("get_orders", {"status": "all", "after": after, "nested": True, "limit": 500}))
    orders = data.get("result", data) if isinstance(data, dict) else data
    return orders if isinstance(orders, list) else []


def print_report(trips: list[dict], still_open: list[dict], days: int, account: str) -> None:
    if not trips:
        print(f"no completed round trips in the last {days} day(s) on account={account}")
        if still_open:
            print(f"({len(still_open)} position(s) still open)")
        return

    s = summarize(trips)
    print(f"=== round trips - last {days} day(s), account={account} ===\n")
    print(f"{'symbol':<21}{'qty':>4}{'entry':>8}{'exit':>8}{'held':>7}{'P&L':>9}{'%':>7}  exit")
    for t in sorted(trips, key=lambda x: x["exit_time"]):
        held = f"{t['hold_minutes']:.0f}m" if t.get("hold_minutes") is not None else "-"
        pct = f"{t['pnl_pct']:.1f}" if t.get("pnl_pct") is not None else "-"
        print(
            f"{t['symbol']:<21}{t['qty']:>4.0f}{t['entry_price']:>8.2f}{t['exit_price']:>8.2f}"
            f"{held:>7}{t['pnl']:>9.2f}{pct:>7}  {REASON_LABEL.get(t['exit_reason'], t['exit_reason'])}"
        )

    pf = s["profit_factor"]
    print(f"\ntrades {s['trades']}   net P&L {s['pnl']:+.2f}   win rate {s['win_rate_pct']}%   median hold {s['median_hold_min']}m")
    print(f"avg win {s['wins']['avg']}   avg loss {s['losses']['avg']}   profit factor {pf if pf is not None else 'n/a (no losses yet)'}")

    print("\nhow trades ended:")
    print(f"  {'reason':<14}{'trades':>7}{'share':>8}{'P&L':>10}{'avg':>9}{'median hold':>13}")
    for reason in EXIT_REASONS:
        r = s["by_exit_reason"][reason]
        if not r["trades"]:
            continue
        hold = f"{r['median_hold_min']}m" if r["median_hold_min"] is not None else "-"
        print(f"  {REASON_LABEL[reason]:<14}{r['trades']:>7}{r['share_pct']:>7}%{r['pnl']:>10.2f}{r['avg_pnl']:>9.2f}{hold:>13}")

    for title, key in (("by underlying", "by_underlying"), ("by instrument", "by_instrument"),
                       ("by DTE at entry", "by_dte_at_entry"), ("by entry hour (ET)", "by_entry_hour")):
        print(f"\n{title}:")
        for name, b in s[key].items():
            print(f"  {name:<8} trades {b['trades']:>3}  wins {b['wins']:>3}  P&L {b['pnl']:>9.2f}  avg {b['avg_pnl']:>8.2f}")

    if still_open:
        print("\nstill open (not counted above):")
        for o in still_open:
            print(f"  {o['symbol']:<21} qty {o['qty']:>4.0f} @ {o['entry_price']:.2f}")

    print(f"\n{s['trades']} trades is a tiny sample - read the exit mix and the cuts, not the P&L.")


async def run(args: argparse.Namespace) -> int:
    validate_account(args.account)
    journal.use_account(args.account)
    overrides.use_account(args.account)
    orders = await fetch_orders(args.account, args.days)
    fills = fills_from_orders(orders, journaled_sells())
    trips, still_open = pair_round_trips(fills)
    if args.json:
        print(json.dumps({"account": args.account, "days": args.days, "summary": summarize(trips),
                          "trips": trips, "still_open": still_open}, indent=2, default=str))
        return 0
    print_report(trips, still_open, args.days, args.account)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--account", default="test", help="named account (official is read-only here, safe any time)")
    ap.add_argument("--json", action="store_true")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""What the model was shown last cycle, and what it did with it.

The journal already holds all of this, but answering "was the menu any good
and did the model quote it honestly" meant three greps and a jq incantation -
and on the trading host there is no jq. This is the one-screen version:
inputs, decision, outcome, for the most recent cycle of one account.

Read-only and journal-only: no credentials, no network, safe on the judged
account at any time, and it works after hours when nothing is running.

    python scripts/last_cycle.py --account official
    python scripts/last_cycle.py --account test --day 2026-09-01
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import journal
from bot.report import _clip

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "c": "\033[36m", "0": "\033[0m"}
EVENTS = ("cycle_start", "predictions", "decision", "order_submitted",
          "order_rejected", "order_error", "dry_run", "order_canceled")


def paint(text: str, colour: str, on: bool) -> str:
    return f"{C[colour]}{text}{C['0']}" if on else text


def cycles(records: list[dict]) -> list[list[dict]]:
    """Split a day's records into cycles.

    `predictions` is journaled just BEFORE `cycle_start` (run_cycle.py logs the
    prior as soon as the snapshot has it), so a naive split on cycle_start puts
    each cycle's prior in the previous group. Start a new group at cycle_start
    and pull a trailing predictions record forward into it."""
    out: list[list[dict]] = []
    for r in records:
        if r.get("event") == "cycle_start":
            carried = []
            if out and out[-1] and out[-1][-1].get("event") == "predictions":
                carried = [out[-1].pop()]
            out.append([*carried, r])
        elif out:
            out[-1].append(r)
    return out


def prior_line(record: dict, underlying: str) -> list[str]:
    entry = record.get(underlying)
    if not isinstance(entry, dict):
        return []
    lines = []
    for label, src in (("Kalshi", entry), ("chain ", entry.get("chain"))):
        if not isinstance(src, dict):
            continue
        if src.get("suppressed"):
            lines.append(f"{label}  withheld - {src['suppressed']}")
            continue
        vals = " ".join(
            f"{name} {src[key]:.3f}" for key, name in
            (("p_above_reference", "P(above)"), ("p_up_over_1pct", "P(up>1%)"),
             ("p_down_over_1pct", "P(down>1%)"))
            if isinstance(src.get(key), (int, float)))
        if vals:
            lines.append(f"{label}  {vals}")
    return lines


def render(cycle: list[dict], colour: bool) -> None:
    def p(text=""):
        print(text)

    start = next((r for r in cycle if r.get("event") == "cycle_start"), {})
    ts = str(start.get("ts") or "")[11:19]
    p(paint(f"cycle {ts} ET   account {start.get('account')}   "
            f"equity {start.get('equity')}   day P&L {start.get('day_pnl'):+.2f}   "
            f"positions {start.get('positions')}", "b", colour))

    coverage = start.get("chain_coverage") or {}
    if coverage:
        p()
        p(paint("  the option chain it was given", "c", colour))
        for name, cov in coverage.items():
            flag = paint("  TRUNCATED", "r", colour) if cov.get("truncated") else ""
            p(f"    {name:<5} {cov.get('contracts'):>5} contracts  {cov.get('pages')} page(s)  "
              f"out to {cov.get('max_dte')} DTE{flag}")

    prior = next((r for r in cycle if r.get("event") == "predictions"), None)
    if prior:
        p()
        p(paint("  the second opinion it was given", "c", colour))
        for name in [k for k in prior if k not in ("ts", "event", "account")]:
            for i, line in enumerate(prior_line(prior, name)):
                p(f"    {name if i == 0 else '     ':<5} {line}")

    decision = next((r for r in cycle if r.get("event") == "decision"), None)
    if decision:
        p()
        usage = decision.get("usage") or {}
        p(paint("  what it decided", "c", colour) +
          f"   {decision.get('count')} proposal(s)   {decision.get('model')}   "
          f"{usage.get('total_tokens')} tokens   {decision.get('latency_sec')}s")
        cites = decision.get("citations")
        if isinstance(cites, dict) and not cites.get("skipped"):
            bad = len(cites.get("unsupported") or []) + len(cites.get("misattributed") or [])
            verdict = f"{cites.get('checked', 0)} prior figure(s) quoted, {bad} unsupported"
            p(f"    {paint(verdict, 'r' if bad else 'g', colour)}")

    p()
    p(paint("  what happened to it", "c", colour))
    outcomes = [r for r in cycle if r.get("event") in EVENTS[3:]]
    if not outcomes:
        p("    nothing - a hold is a decision")
    for r in outcomes:
        event = r["event"]
        mark, tone = {
            "order_submitted": ("SUBMITTED", "g"), "dry_run": ("DRY-RUN  ", "y"),
            "order_rejected": ("REJECTED ", "r"), "order_error": ("ERROR    ", "r"),
            "order_canceled": ("CANCELLED", "y"),
        }[event]
        head = f"    {paint(mark, tone, colour)} {r.get('side')} {r.get('qty')} {r.get('symbol')}"
        if r.get("resolved_from"):
            head += paint(f"  (model wrote {r['resolved_from']})", "dim", colour)
        p(head)
        if r.get("detail"):
            p(paint(f"        why: {_clip(r['detail'], 160)}", "dim", colour))
        if r.get("reason"):
            p(paint(f"        {_clip(r['reason'], 220)}", "dim", colour))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="official")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default today, Eastern)")
    ap.add_argument("--n", type=int, default=1, help="how many recent cycles to show")
    ap.add_argument("--json", action="store_true", help="the raw records instead")
    args = ap.parse_args()

    journal.use_account(args.account)
    grouped = cycles(journal.read_events(args.day, events=EVENTS))
    if not grouped:
        print(f"no cycles journaled for {args.account} on {args.day or 'today'}", file=sys.stderr)
        return 1

    chosen = grouped[-max(1, args.n):]
    if args.json:
        print(json.dumps(chosen, indent=2, default=str))
        return 0

    colour = sys.stdout.isatty()
    for i, cycle in enumerate(chosen):
        if i:
            print()
        render(cycle, colour)
    return 0


if __name__ == "__main__":
    sys.exit(main())

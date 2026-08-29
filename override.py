#!/usr/bin/env python3
"""Runtime config overrides - intraday tweaks without a deploy.

Overrides beat config.yaml for an allowlisted set of strategy/model/exit
knobs and EXPIRE at the end of the trading day (16:00 ET) unless --until is
given. Durable changes still go through config.yaml + a PR. See
bot/overrides.py for the why.

Usage:
  override.py show
  override.py set <key> <value> [--until HH:MM | YYYY-MM-DDTHH:MM]
  override.py set strategy_notes @notes.txt        # read a multi-line value from a file
  override.py clear <key>
  override.py clear --all
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from bot import journal, overrides
from bot.config import load_config
from bot.risk import EASTERN


def parse_until(text: str | None, now: datetime) -> datetime | None:
    if not text:
        return None
    if len(text) == 5 and text[2] == ":":  # HH:MM today, Eastern
        hour, minute = text.split(":")
        return now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    until = datetime.fromisoformat(text)
    return until if until.tzinfo else until.replace(tzinfo=EASTERN)


def read_value(text: str) -> str:
    return Path(text[1:]).read_text() if text.startswith("@") else text


def show() -> int:
    now = datetime.now(EASTERN)
    active = overrides.active_overrides(now)
    config = load_config(now=now)
    print(f"== Active overrides ({len(active)}) == (now {now:%Y-%m-%d %H:%M %Z})")
    for key, entry in sorted(active.items()):
        value = entry["value"]
        shown = (value.strip().splitlines()[0] + " ...") if isinstance(value, str) and "\n" in value else value
        print(f"  {key} = {shown!r}  until {entry.get('until')}  (set by {entry.get('set_by')} at {entry.get('set_at')})")
    if not active:
        print("  (none - running pure config.yaml)")
    print("\n== Effective values of overridable keys ==")
    for key in sorted(overrides.OVERRIDABLE_KEYS):
        value = config.get(key)
        shown = (value.strip().splitlines()[0] + " ...") if isinstance(value, str) and "\n" in value else value
        marker = "*" if key in active else " "
        print(f" {marker} {key} = {shown!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p_set = sub.add_parser("set")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_set.add_argument("--until", help="HH:MM (today, ET) or an ISO datetime; default 16:00 ET today")
    p_clear = sub.add_parser("clear")
    p_clear.add_argument("key", nargs="?")
    p_clear.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.cmd == "show":
        return show()

    now = datetime.now(EASTERN)
    if args.cmd == "set":
        try:
            entry = overrides.set_override(args.key, read_value(args.value), until=parse_until(args.until, now), now=now)
        except (ValueError, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        journal.log("override_set", key=args.key, value=entry["value"], until=entry["until"], set_by="cli")
        print(f"{args.key} = {entry['value']!r} until {entry['until']}")
        return 0

    if args.all:
        n = overrides.clear_all()
        journal.log("override_cleared", key="*", count=n, set_by="cli")
        print(f"cleared {n} override(s) - back to config.yaml")
        return 0
    if not args.key:
        print("clear needs a key or --all", file=sys.stderr)
        return 2
    existed = overrides.clear_override(args.key)
    journal.log("override_cleared", key=args.key, existed=existed, set_by="cli")
    print(f"{args.key}: {'cleared' if existed else 'was not set'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

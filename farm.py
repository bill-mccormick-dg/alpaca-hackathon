#!/usr/bin/env python3
"""Farm runner: one container = one variant = one paper account (issue #13).

CT 108 uses cron; a container has none, so this loop does the same job:
run_cycle every N minutes inside market hours, the expiring-only flatten
once at flatten_at, the end-of-day review once at review_at (Eastern).
Each entrypoint is a subprocess, exactly as cron would run it, so nothing
about the bot changes between the farm and production.

Usage:
  farm.py --account qwen-a --config config-variants/qwen.yaml [--cycle-minutes 10]
  farm.py ... --once            run one cycle now and exit (smoke test)
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from bot.credentials import validate_account
from bot.risk import EASTERN

HERE = Path(__file__).resolve().parent
PY = sys.executable


def _t(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def next_actions(now: datetime, last: dict, cycle_minutes: int, window: tuple[dtime, dtime],
                 flatten_at: dtime, review_at: dtime) -> list[str]:
    """Which of cycle / flatten / review are due at `now` (Eastern), given
    what already ran today (`last`: name -> datetime). Pure, so it's
    testable without sleeping. Weekends: nothing."""
    if now.weekday() >= 5:
        return []
    due = []
    t = now.time()
    day = now.date()

    def ran_today(name):
        d = last.get(name)
        return d is not None and d.date() == day

    if window[0] <= t <= window[1]:
        lc = last.get("cycle")
        if lc is None or (now - lc) >= timedelta(minutes=cycle_minutes):
            due.append("cycle")
    if t >= flatten_at and not ran_today("flatten"):
        due.append("flatten")
    if t >= review_at and not ran_today("review"):
        due.append("review")
    return due


def run_entry(name: str, account: str, config: str, extra: list[str] | None = None) -> int:
    script = {"cycle": "run_cycle.py", "flatten": "flatten.py", "review": "eod_review.py"}[name]
    cmd = [PY, str(HERE / script), "--account", account, "--config", config] + (extra or [])
    if name == "flatten":
        cmd.append("--expiring-only")
    print(f"[farm] {datetime.now(EASTERN):%H:%M:%S} {' '.join(cmd[1:])}", flush=True)
    return subprocess.call(cmd, cwd=HERE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--cycle-minutes", type=int, default=10)
    ap.add_argument("--window", default="09:00-15:50", help="Eastern; run_cycle gates itself inside this")
    ap.add_argument("--flatten-at", default="15:50")
    ap.add_argument("--review-at", default="16:05")
    ap.add_argument("--once", action="store_true", help="run one cycle now (with --force --dry-run unless --live) and exit")
    ap.add_argument("--live", action="store_true", help="with --once: real orders on the account")
    args = ap.parse_args()
    validate_account(args.account)

    if args.once:
        extra = [] if args.live else ["--dry-run", "--force"]
        return run_entry("cycle", args.account, args.config, extra)

    lo, hi = (_t(x) for x in args.window.split("-"))
    last: dict = {}
    print(f"[farm] account={args.account} config={args.config} every {args.cycle_minutes}m "
          f"{args.window} ET, flatten {args.flatten_at}, review {args.review_at}", flush=True)
    while True:
        now = datetime.now(EASTERN)
        for name in next_actions(now, last, args.cycle_minutes, (lo, hi), _t(args.flatten_at), _t(args.review_at)):
            run_entry(name, args.account, args.config)
            last[name] = datetime.now(EASTERN)
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())

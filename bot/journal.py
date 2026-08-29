"""JSONL trade journal under logs/ - mirrors alpaca-trader's trader/journal.py.

One record per event, appended to logs/journal.jsonl. This is the single
"something happened" chokepoint: run_cycle, flatten, and status all emit
or read through here, so a future side channel (the MQTT publish for the
Home Assistant integration, issue #14) hooks in at log() and nowhere else.
"""

import json
from datetime import datetime
from pathlib import Path

from bot.risk import EASTERN, LOGS_DIR

JOURNAL = LOGS_DIR / "journal.jsonl"


def log(event: str, journal: Path = JOURNAL, **fields) -> dict:
    journal.parent.mkdir(exist_ok=True)
    record = {"ts": datetime.now(EASTERN).isoformat(timespec="seconds"), "event": event, **fields}
    with journal.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return record


def read_events(
    day: str | None = None, events: tuple[str, ...] | None = None, journal: Path = JOURNAL
) -> list[dict]:
    """Parsed records for one Eastern-time date (default today; "all" for
    the whole journal), optionally filtered by event name. Malformed lines
    are skipped, never fatal - a half-written line from a crash must not
    take the summary down with it."""
    day = day or datetime.now(EASTERN).date().isoformat()
    if not journal.exists():
        return []
    out = []
    with journal.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if day != "all" and not str(r.get("ts", "")).startswith(day):
                continue
            if events and r.get("event") not in events:
                continue
            out.append(r)
    return out


def daily_summary(day: str | None = None, journal: Path = JOURNAL) -> dict:
    day = day or datetime.now(EASTERN).date().isoformat()
    summary = {
        "date": day,
        "cycles": 0,
        "orders": 0,
        "rejected": 0,
        "errors": 0,
        "equity": None,
        "day_pnl": None,
        "halts": [],
        "trades": [],
    }
    for r in read_events(day, journal=journal):
        ev = r.get("event")
        if ev == "cycle_start":
            summary["cycles"] += 1
        elif ev == "order_submitted":
            summary["orders"] += 1
            summary["trades"].append(
                {k: r.get(k) for k in ("ts", "side", "qty", "symbol", "instrument", "reason")}
            )
        elif ev == "order_rejected":
            summary["rejected"] += 1
        elif ev in ("error", "order_error"):
            summary["errors"] += 1
        elif ev in ("daily_loss_halt", "manual_halt"):
            summary["halts"].append(ev)
        if "equity" in r:
            summary["equity"] = r["equity"]
        if "day_pnl" in r:
            summary["day_pnl"] = r["day_pnl"]
    return summary

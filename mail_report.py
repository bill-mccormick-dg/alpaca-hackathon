#!/usr/bin/env python3
"""Hourly email report for the team, with CSV attachments (issue #87).

The Home Assistant view (over Tailscale) is the live picture; this is the
push half - it reaches teammates who are not going to sit on a dashboard,
and the CSVs give them the raw rows to pivot themselves rather than trusting
a rendering.

Mail goes out through the central Postfix relay LXC, the same path
`daily-status.py` on the transcode worker already uses: hand the message to
the local Postfix null client on localhost:25 and let the relay do the
smarthost work. No credentials live here.

Read-only and completely out of band: this runs as its own cron entry, so
nothing it does can slow or fail a trading cycle. A broken report is a
missing email, never a missed trade.

Recipients are read from the deployed credentials file, NOT from config.yaml
- teammate email addresses are personal data and this repo goes public before
Sep 4.

  mail_report.py --account official
  mail_report.py --account test --config config-test.yaml --dry-run
"""

import argparse
import csv
import io
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage

from bot import credentials, journal, report
from bot.config import load_config
from bot.risk import EASTERN, LOGS_DIR, RiskManager

# Settings resolved from the account's deployed credentials file, then the
# environment - the same chain and the same reason as bot/credentials.py's
# load_mqtt_env(): cron inherits almost no environment, so a plain os.environ
# lookup silently finds nothing and the report never sends.
REPORT_KEYS = ("REPORT_EMAIL_TO", "REPORT_EMAIL_FROM", "REPORT_SMTP_HOST", "REPORT_SMTP_PORT")

TRADE_COLUMNS = ("ts", "event", "side", "qty", "symbol", "price", "exit", "order_id", "reason")
CYCLE_COLUMNS = ("ts", "equity", "day_pnl", "positions", "halt")


def load_report_env(account: str) -> dict:
    """REPORT_* settings from the deployed credentials file, then env vars.

    Uses bot.credentials' own file resolution so there is one definition of
    where an account's settings live. Reaches for the module's file parser
    directly rather than adding a REPORT_ loader to bot/credentials.py: this
    is a reporting feature and has no business changing trading code."""
    found = {k: os.environ[k] for k in REPORT_KEYS if os.environ.get(k)}
    try:
        path = credentials.credentials_file(account)
        if path.is_file():
            # Private helper on purpose - see this function's docstring.
            parsed = credentials._parse_env_file(path)
            for key in REPORT_KEYS:
                if key not in found and parsed.get(key):
                    found[key] = parsed[key]
    except OSError:
        pass
    return found


def recipients(settings: dict) -> list[str]:
    raw = settings.get("REPORT_EMAIL_TO", "")
    return [addr.strip() for addr in raw.replace(";", ",").split(",") if addr.strip()]


def _ts_local(value, tz):
    """A journal timestamp rewritten onto `tz`, offset kept - lossless (the
    ISO offset travels with it), just readable against the body and the cron
    logs without mental arithmetic. Anything unparseable passes through."""
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return value
    if dt.tzinfo is None:
        return value
    return dt.astimezone(tz).isoformat(timespec="seconds")


def _rows(events, columns, keep, tz=None) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for record in events:
        if keep(record):
            row = {c: record.get(c, "") for c in columns}
            if tz and row.get("ts"):
                row["ts"] = _ts_local(row["ts"], tz)
            writer.writerow(row)
    return buf.getvalue()


def trades_csv(events, tz=None) -> str:
    """Every order-shaped event, with the reason the model gave."""
    return _rows(events, TRADE_COLUMNS, lambda r: r.get("event") in report.TRADE_EVENTS, tz)


def cycles_csv(events, tz=None) -> str:
    """One row per cycle - the intra-day equity curve, for a pivot table."""
    return _rows(events, CYCLE_COLUMNS, lambda r: r.get("event") == "cycle_start", tz)


def equity_csv(path=None) -> str:
    """The multi-day equity log eod_review.py appends to, if it exists."""
    path = path or (LOGS_DIR / "equity.jsonl")
    try:
        lines = [line for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        return ""

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    if not records:
        return ""
    columns = sorted({k for r in records for k in r})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue()


def _is_activity(record: dict) -> bool:
    """A journal event that represents something happening to money.

    Trade-shaped events always count. A `flatten` event counts only when it
    actually touched a position: flatten's closes never journal as orders
    (bot/flatten.py writes no journal events), so without this the hour the
    bot closed everything would read as "no trades" - while the 14:50
    backstop logs an empty flatten every day, which is exactly the quiet
    hour suppression exists for."""
    event = record.get("event")
    if event in report.TRADE_EVENTS:
        return True
    return event == "flatten" and bool(record.get("closed") or record.get("attempted"))


def suppress_reason(events, now: datetime, halt: str, force: bool = False, window_minutes: int = 60) -> str | None:
    """Why the hourly email should NOT go out, or None to send (issue #153).

    Suppression is the exception, so anything unusual falls toward sending:
    a halt (any value but "none" - including "unknown" when config is
    unreadable) must always reach the team, --force always wins, and an
    unparseable timestamp is skipped rather than trusted. Journal timestamps
    are always offset-aware (bot/journal.py stamps Eastern ISO), so the
    comparison is aware-vs-aware regardless of the clock `now` is on."""
    if force or halt != "none":
        return None
    cutoff = now - timedelta(minutes=window_minutes)
    for record in events:
        if not _is_activity(record):
            continue
        try:
            ts = datetime.fromisoformat(str(record.get("ts")))
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is not None and ts >= cutoff:
            return None
    return f"no trading activity in the last {window_minutes}m"


def summarize(events, account: str, halt: str = "none", tz=None) -> dict:
    """The numbers that go in the subject line and the top of the body.

    `halt` is the CURRENT state, derived from the halt files by the caller -
    not from journal events. A manual_halt event stays in today's journal
    after the halt is cleared, so an event-derived flag would mark the
    account halted for the rest of the day and teach everyone to ignore the
    subject line. Same reasoning as RiskManager.halt_state() (#74).

    `tz` renders trade times on that clock; journal timestamps are Eastern."""
    cycles = [r for r in events if r.get("event") == "cycle_start"]
    latest = cycles[-1] if cycles else {}
    trades = report.recent_trades(events, limit=0, tz=tz)
    errors = [r for r in events if r.get("event") == "error"]
    retries = [r for r in events if r.get("event") == "decide_retry"]
    equity = latest.get("equity")
    start = latest.get("start_of_day_equity")
    day_pnl = latest.get("day_pnl")
    if day_pnl is None and equity is not None and start is not None:
        day_pnl = float(equity) - float(start)
    return {
        "account": account,
        "equity": equity,
        "day_pnl": day_pnl,
        "positions": latest.get("positions"),
        "cycles": len(cycles),
        "filled": sum(1 for t in trades if t["event"] == "order_submitted"),
        "rejected": sum(1 for t in trades if t["event"] == "order_rejected"),
        "dry_run": sum(1 for t in trades if t["event"] == "dry_run"),
        "errors": len(errors),
        "retries": len(retries),
        "halt": halt,
        "halted": halt != "none",
        "trades": trades,
    }


def _money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def subject_line(s: dict, now: datetime) -> str:
    pnl = ""
    if s["day_pnl"] is not None:
        try:
            pnl = f" {float(s['day_pnl']):+,.2f}"
        except (TypeError, ValueError):
            pnl = ""
    flag = f" [HALTED: {s['halt']}]" if s["halted"] else ""
    return f"Autobelay — {s['account']} — {now:%H:%M %Z} — {_money(s['equity'])}{pnl}{flag}"


def body_text(s: dict, now: datetime) -> str:
    lines = [
        f"Autobelay — account: {s['account']}",
        f"As of {now:%Y-%m-%d %H:%M %Z}",
        "",
        f"  Equity          {_money(s['equity'])}",
        f"  Day P&L         {_money(s['day_pnl'])}",
        f"  Open positions  {s['positions'] if s['positions'] is not None else 'n/a'}",
        f"  Cycles today    {s['cycles']}",
        f"  Orders          {s['filled']} filled, {s['rejected']} rejected, {s['dry_run']} dry-run",
    ]
    if s["errors"] or s["retries"]:
        lines.append(f"  Model           {s['errors']} error(s), {s['retries']} retry/ies")
    if s["halted"]:
        lines.append("")
        lines.append(f"  *** THIS ACCOUNT IS HALTED ({s['halt']}) — it is not trading. ***")
    lines += ["", "Trades so far today", "-------------------", ""]
    # All of today's trades, reasons unclipped (reason_chars=0): the 12-entry
    # and 400-char caps exist for Home Assistant's sensor limits, and in an
    # email they cut entries mid-sentence under a heading that says "today".
    lines.append(report.render_trades_markdown(s["trades"], s["account"], reason_chars=0))
    lines += [
        "",
        "",
        "Attached: trades (every order with the model's reasoning), cycles",
        "(the intra-day equity curve) and the multi-day equity log, as CSV.",
        "",
        "This is an automated read-only report. Nothing here can be replied to",
        "or acted on; the bot is controlled from CT 108 only.",
    ]
    return "\n".join(lines)


def build_message(summary: dict, attachments: dict, settings: dict, now: datetime) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject_line(summary, now)
    msg["From"] = settings.get("REPORT_EMAIL_FROM") or "ai-day-trader@localhost"
    msg["To"] = ", ".join(recipients(settings))
    msg["Auto-Submitted"] = "auto-generated"  # keep vacation responders quiet
    msg.set_content(body_text(summary, now))
    for filename, content in attachments.items():
        if not content:
            continue
        msg.add_attachment(
            content.encode("utf-8"), maintype="text", subtype="csv", filename=filename
        )
    return msg


def send(msg: EmailMessage, settings: dict) -> None:
    host = settings.get("REPORT_SMTP_HOST") or "localhost"
    port = int(settings.get("REPORT_SMTP_PORT") or 25)
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.send_message(msg)


def run(args: argparse.Namespace) -> int:
    credentials.validate_account(args.account)
    journal.use_account(args.account)
    settings = load_report_env(args.account)
    to = recipients(settings)
    if not to and not args.dry_run:
        print(
            f"no REPORT_EMAIL_TO for account {args.account!r} - nothing to send "
            f"(checked {credentials.credentials_file(args.account)} and the environment)",
            file=sys.stderr,
        )
        return 0

    # The host's local clock (America/Chicago on CT 108), not Eastern: the
    # subject, the "As of" line and the trade times should agree with the
    # cron logs and the team's own clocks, not cite EDT. Market logic below
    # (halt_state) still runs on Eastern via its own conversion.
    now = datetime.now().astimezone()
    events = journal.read_events()
    # Current halt state comes from the halt FILES via the same helper the
    # dashboard uses, never from journal events - see summarize().
    try:
        halt = RiskManager(load_config(args.config), account=args.account).halt_state(now)
    except Exception as exc:  # noqa: BLE001 - a report must still send if config is unreadable
        print(f"halt state unavailable ({type(exc).__name__}: {exc})", file=sys.stderr)
        halt = "unknown"
    reason = suppress_reason(events, now, halt, force=args.force, window_minutes=args.window_minutes)
    if reason:
        tag = "[suppressed] " if args.dry_run else ""
        print(f"{tag}{reason} for account {args.account!r} — skipping email (--force sends anyway)")
        return 0

    summary = summarize(events, args.account, halt, tz=now.tzinfo)
    day = now.astimezone(EASTERN).date().isoformat()
    attachments = {
        f"trades-{day}-{args.account}.csv": trades_csv(events, tz=now.tzinfo),
        f"cycles-{day}-{args.account}.csv": cycles_csv(events, tz=now.tzinfo),
        f"equity-{args.account}.csv": equity_csv(),
    }
    msg = build_message(summary, attachments, settings, now)

    if args.dry_run:
        print(msg["Subject"])
        print()
        print(msg.get_body(preferencelist=("plain",)).get_content())
        for name, content in attachments.items():
            rows = max(len(content.splitlines()) - 1, 0)
            print(f"[attachment] {name}: {rows} row(s), {len(content)} bytes")
        print(f"[would send to] {', '.join(to) or '(no recipients configured)'}")
        return 0

    send(msg, settings)
    print(f"sent to {', '.join(to)}: {msg['Subject']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="test", help="named account: official, test, or a variant")
    ap.add_argument("--config", default=None, help="config file (default config.yaml) - read for the halt-file paths")
    ap.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    ap.add_argument("--force", action="store_true",
                    help="send even with no recent activity - the cron's 15:00 wrap-up entry uses this")
    ap.add_argument("--window-minutes", type=int, default=60,
                    help="how far back to look for trading activity before suppressing (default 60)")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())

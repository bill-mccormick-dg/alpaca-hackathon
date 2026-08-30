"""Render the journal into something a teammate can read (issue #87).

Everything that says what the bot is doing lives in logs/journal*.jsonl on
CT 108, which is git-ignored, excluded from the deploy rsync, and reachable
only by SSH. A remote teammate had no way to answer "is it working, and what
is it doing?" without asking someone with host access.

These functions turn the journal into a rolling trade list and reuse the
end-of-day digest, so bot/mqtt.py can publish both to Home Assistant - which
teammates reach over Tailscale. Kept pure and free of I/O so the rendering is
testable without a broker or a journal file.

Home Assistant constraint that shapes all of this: a sensor's STATE is capped
at 255 characters. Anything longer has to travel as a JSON attribute, so each
payload here is {state, attributes} - a short summary for the state, the full
markdown for a card to render.
"""

from datetime import datetime

# The journal events that represent something actually happening to money,
# in the order a reader cares about them.
TRADE_EVENTS = ("order_submitted", "order_rejected", "order_error", "dry_run")

# HA's hard limit on a sensor's state string.
MAX_STATE_CHARS = 255

DEFAULT_TRADE_LIMIT = 12


def _fmt_time(ts: str) -> str:
    """HH:MM from a journal ISO timestamp, falling back to the raw value."""
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M")
    except (TypeError, ValueError):
        return str(ts or "")[:5]


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def recent_trades(events, limit: int = DEFAULT_TRADE_LIMIT) -> list[dict]:
    """The most recent trade-shaped journal events, newest last.

    `events` is any iterable of journal records - the caller decides whether
    that is today's or a wider window."""
    trades = []
    for record in events:
        event = record.get("event")
        if event not in TRADE_EVENTS:
            continue
        trades.append(
            {
                "time": _fmt_time(record.get("ts")),
                "event": event,
                "side": record.get("side") or "",
                "qty": record.get("qty"),
                "symbol": record.get("symbol") or "",
                "price": record.get("price"),
                "reason": record.get("reason") or record.get("detail") or "",
                "exit": bool(record.get("exit")),
                "order_id": record.get("order_id") or "",
            }
        )
    return trades[-limit:] if limit else trades


def _trade_line(t: dict) -> str:
    verb = {
        "order_submitted": "FILLED",
        "order_rejected": "rejected",
        "order_error": "ERROR",
        "dry_run": "dry-run",
    }.get(t["event"], t["event"])
    qty = f"x{t['qty']}" if t.get("qty") is not None else ""
    price = f" @ ${float(t['price']):.2f}" if t.get("price") is not None else ""
    tag = " (exit)" if t.get("exit") else ""
    head = f"**{t['time']}** · {verb}{tag} {t['side']} {qty} `{t['symbol']}`{price}".replace("  ", " ")
    reason = _clip(t.get("reason"), 160)
    return f"{head}\n  {reason}" if reason else head


def render_trades_markdown(trades: list[dict], account: str = "") -> str:
    """A markdown card body for the trade list. Newest last, so it reads like
    a log rather than a leaderboard."""
    if not trades:
        who = f" for **{account}**" if account else ""
        return f"_No trades yet today{who}._"
    return "\n\n".join(_trade_line(t) for t in trades)


def trades_summary(trades: list[dict]) -> str:
    """The short line that becomes the sensor STATE - must fit in 255 chars."""
    if not trades:
        return "no trades"
    filled = sum(1 for t in trades if t["event"] == "order_submitted")
    rejected = sum(1 for t in trades if t["event"] == "order_rejected")
    dry = sum(1 for t in trades if t["event"] == "dry_run")
    parts = []
    if filled:
        parts.append(f"{filled} filled")
    if rejected:
        parts.append(f"{rejected} rejected")
    if dry:
        parts.append(f"{dry} dry-run")
    return _clip(", ".join(parts) or "no trades", MAX_STATE_CHARS)


def trades_payload(events, account: str = "", limit: int = DEFAULT_TRADE_LIMIT) -> dict:
    """{state, attributes} for the recent-trades sensor."""
    trades = recent_trades(events, limit)
    return {
        "state": trades_summary(trades),
        "attributes": {
            "markdown": render_trades_markdown(trades, account),
            "trades": trades,
            "count": len(trades),
            "account": account,
        },
    }


def eod_payload(markdown: str, day: str = "", account: str = "") -> dict:
    """{state, attributes} for the end-of-day summary sensor.

    The digest markdown is reused verbatim - eod_review.py already writes it
    to logs/eod/<date>-<account>.md, and a second rendering would be a second
    thing to keep in step."""
    text = str(markdown or "").strip()
    return {
        "state": _clip(f"{day} digest" if day else "digest", MAX_STATE_CHARS) if text else "none",
        "attributes": {
            "markdown": text or "_No end-of-day digest yet._",
            "day": day,
            "account": account,
        },
    }

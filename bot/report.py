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

import json
from datetime import datetime

# The journal events that represent something actually happening to money,
# in the order a reader cares about them.
TRADE_EVENTS = ("order_submitted", "order_rejected", "order_error", "dry_run")

# HA's hard limit on a sensor's state string.
MAX_STATE_CHARS = 255

DEFAULT_TRADE_LIMIT = 12
REASON_CHARS = 400
VERDICT_PREFIX = "why: "


def _fmt_time(ts: str, tz=None) -> str:
    """HH:MM from a journal ISO timestamp, falling back to the raw value.

    Journal timestamps are written in Eastern (bot/journal.py); pass `tz` to
    render them on another clock - the email shows the host's local time so
    its lines agree with the cron logs and the viewer."""
    try:
        dt = datetime.fromisoformat(ts)
        if tz is not None and dt.tzinfo is not None:
            dt = dt.astimezone(tz)
        return dt.strftime("%H:%M")
    except (TypeError, ValueError):
        return str(ts or "")[:5]


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if not limit:
        return text
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def recent_trades(events, limit: int = DEFAULT_TRADE_LIMIT, tz=None) -> list[dict]:
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
                "time": _fmt_time(record.get("ts"), tz),
                "event": event,
                "side": record.get("side") or "",
                "qty": record.get("qty"),
                "symbol": record.get("symbol") or "",
                "price": record.get("price"),
                # The model's reason and the funnel's verdict are different
                # facts; a rejection needs both. `reason or detail` used to
                # collapse them, so an email said "rejected" and then quoted
                # the model's case for the trade - never why it was refused.
                "reason": record.get("reason") or "",
                "detail": record.get("detail") or "",
                "exit": bool(record.get("exit")),
                "order_id": record.get("order_id") or "",
            }
        )
    return trades[-limit:] if limit else trades


def _trade_line(t: dict, reason_chars: int = REASON_CHARS) -> str:
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
    lines = [head]
    # Why the funnel refused / what broke - first, because it is the fact a
    # reader of "rejected" is looking for.
    if t["event"] in ("order_rejected", "order_error") and t.get("detail"):
        lines.append(f"  {VERDICT_PREFIX}{_clip(t['detail'], reason_chars)}")
    # The default is 400, not 160: the model's reasons run 150-250 chars, and
    # at 160 nearly every one ended in "…" one clause before the point. That
    # cap is for the HA card; a caller with no size constraint (the email)
    # passes reason_chars=0 for the full text.
    if t.get("reason"):
        lines.append(f"  {_clip(t['reason'], reason_chars)}")
    return "\n".join(lines)


def render_trades_markdown(trades: list[dict], account: str = "", reason_chars: int = REASON_CHARS) -> str:
    """A markdown card body for the trade list. Newest last, so it reads like
    a log rather than a leaderboard. reason_chars=0 disables clipping."""
    if not trades:
        who = f" for **{account}**" if account else ""
        return f"_No trades yet today{who}._"
    return "\n\n".join(_trade_line(t, reason_chars) for t in trades)


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


FEED_LINES = 40


def render_feed_line(r: dict) -> str:
    """One journal record -> one line, in the idiom of the terminal watcher.

    Every event renders - this is the complete stream, unlike the trade list
    above - and unknown events fall through to name + fields rather than
    vanishing, so a new journal event is visible in the feed before anyone
    teaches this function about it."""
    t = _fmt_time(r.get("ts"))
    e = r.get("event") or "?"
    if e == "cycle_start":
        dry = " [DRY RUN]" if r.get("dry_run") else ""
        return f"{t} ▶ cycle  equity {r.get('equity')}  P&L {r.get('day_pnl')}  pos {r.get('positions')}{dry}"
    if e == "config":
        return f"{t}   model {r.get('model')}  review {r.get('review_model')}  hash {r.get('config_hash')}"
    if e == "predictions":
        bits = []
        for sym in ("SPY", "QQQ"):
            p = r.get(sym)
            if isinstance(p, dict):
                verdict = f"withheld ({p.get('suppressed')})" if p.get("suppressed") else "shown"
                bits.append(f"{sym} ref {p.get('reference_close')} P(above) {p.get('p_above_reference')} -> {verdict}")
        return f"{t} ◈ prior  " + ("; ".join(bits) or "none")
    if e == "tool_call":
        return f"{t}   · {r.get('tool')} -> {r.get('result_chars')} chars"
    if e == "decision":
        return f"{t} ✱ model  {r.get('count')} proposal(s)  {r.get('model')}  {(r.get('usage') or {}).get('total_tokens')} tok"
    if e in ("order_submitted", "dry_run"):
        mark = "✓ FILLED" if e == "order_submitted" else "⋯ dry"
        exit_tag = " (exit)" if r.get("exit") else ""
        return f"{t} {mark}{exit_tag} {r.get('side')} {r.get('qty')} {r.get('symbol')} @ {r.get('price')} — {_clip(r.get('reason'), 160)}"
    if e in ("order_rejected", "order_error"):
        return f"{t} ✗ {e.split('_')[1].upper()} {r.get('side')} {r.get('qty')} {r.get('symbol')} — {r.get('detail')}"
    if e == "cycle_end":
        return f"{t} ◀ end, {r.get('actions')} action(s)"
    if e in ("manual_halt", "daily_loss_halt"):
        return f"{t} ■ HALT ({e})"
    if e == "manual_resume":
        return f"{t} ▶ resume"
    if e in ("override_set", "override_cleared"):
        return f"{t} ⚙ {e.split('_')[1]} {r.get('key')} = {_clip(r.get('value'), 60)} ({r.get('set_by')})"
    if e == "error":
        return f"{t} ! error in {r.get('where')}: {_clip(r.get('detail'), 140)}"
    rest = {k: v for k, v in r.items() if k not in ("ts", "event")}
    return f"{t} {e} {_clip(json.dumps(rest, default=str), 120)}"


def feed_payload(events, account: str = "") -> dict:
    """The attribute-sensor payload for the live journal feed (issue #134):
    the last FEED_LINES records rendered one per line, fenced so Home
    Assistant's markdown card keeps it monospace and never interprets a
    reason's underscores as emphasis. Same state/attributes shape as
    trades_payload - HA caps state at 255 chars, the body rides attributes."""
    records = list(events)[-FEED_LINES:]
    lines = [render_feed_line(r) for r in records]
    body = "```text\n" + "\n".join(lines) + "\n```" if lines else ""
    state = _clip(lines[-1] if lines else "no events yet", MAX_STATE_CHARS)
    return {"state": state, "attributes": {"markdown": body, "account": account, "count": len(records)}}


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

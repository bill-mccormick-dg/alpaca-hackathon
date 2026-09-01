"""End-of-day digest - the pure parts of eod_review.py.

Given one day's journal records plus the trade report's summary, produce
the facts a human (or the model, for its one-line recommendation) needs
to decide what to change tomorrow. No network here; eod_review.py does
the fetching and the model call. Everything is a plain dict so it can be
rendered as markdown, JSON, or fed straight back into a prompt.
"""

from collections import Counter
from datetime import datetime

from bot.risk import EASTERN

REJECTION_TOP_N = 8


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def equity_facts(records: list[dict]) -> dict:
    """Start / end equity for the day from cycle_start events, in order."""
    starts = [r for r in records if r.get("event") == "cycle_start" and _num(r.get("equity")) is not None]
    if not starts:
        return {"cycles": 0, "equity_open": None, "equity_close": None, "day_pnl": None, "day_pnl_pct": None}
    first, last = starts[0], starts[-1]
    sod = _num(first.get("equity")) - (_num(first.get("day_pnl")) or 0.0)
    close = _num(last.get("equity"))
    pnl = close - sod if sod is not None else None
    return {
        "cycles": len(starts),
        "equity_open": round(sod, 2) if sod is not None else None,
        "equity_close": round(close, 2),
        "day_pnl": round(pnl, 2) if pnl is not None else None,
        "day_pnl_pct": round(pnl / sod * 100, 3) if pnl is not None and sod else None,
        "first_cycle": first.get("ts"),
        "last_cycle": last.get("ts"),
    }


def _rejection_key(detail: str) -> str:
    """Group rejection reasons by their rule, not their numbers: 'position
    value 5120.00 exceeds max_position_usd 5000' -> 'exceeds max_position_usd'."""
    d = str(detail or "").lower()
    for key in ("not in underlyings whitelist", "exceeds max_position_usd", "max_positions", "max_contracts_per_order",
                "days to expiration", "entries not allowed", "cannot sell", "invalid", "qty must be", "price must be",
                "not a valid occ", "already resting", "expired"):
        if key in d:
            return key
    return d[:60] or "unknown"


def decision_audit(records: list[dict]) -> dict:
    decisions = [r for r in records if r.get("event") == "decision"]
    holds = sum(1 for r in decisions if (r.get("count") or 0) == 0)
    proposals = sum(int(r.get("count") or 0) for r in decisions)
    submitted = [r for r in records if r.get("event") == "order_submitted"]
    rejected = [r for r in records if r.get("event") == "order_rejected"]
    errors = [r for r in records if r.get("event") in ("error", "order_error")]
    dry = [r for r in records if r.get("event") == "dry_run"]

    rej_by_rule = Counter(_rejection_key(r.get("detail")) for r in rejected)
    rej_by_symbol = Counter(r.get("symbol") for r in rejected)
    # One verbatim detail per rule (#155). The key is a grouping label, and
    # on 2026-08-31 the reviewer read 'price must be' as limit prices too
    # aggressive to fill, when the detail was 'price must be positive' - the
    # funnel could not price the contract at all - and recommended widening
    # the strike band for a bug that was already fixed. Count by the key,
    # reason from the example.
    rej_examples: dict[str, str] = {}
    for r in rejected:
        rej_examples.setdefault(_rejection_key(r.get("detail")), str(r.get("detail") or "")[:200])

    usage_in = sum(int((r.get("usage") or {}).get("prompt_tokens") or 0) for r in decisions)
    usage_out = sum(int((r.get("usage") or {}).get("completion_tokens") or 0) for r in decisions)
    latencies = [_num(r.get("latency_sec")) for r in decisions if _num(r.get("latency_sec")) is not None]
    models = Counter(r.get("model") for r in decisions if r.get("model"))
    truncations = sum(1 for r in decisions if r.get("finish_reason") == "length")

    exits = [r for r in submitted if r.get("exit")]
    exit_reasons = Counter(str(r.get("reason") or "").split(" ")[0] for r in exits)

    # Prior citations (#172): did the model's reasons quote numbers it was
    # given? None for the whole day means no cycle carried the field (older
    # journals) - distinct from "audited, all clean".
    cites = [(r.get("ts"), r["citations"]) for r in decisions if isinstance(r.get("citations"), dict)]
    audited = [(ts, c) for ts, c in cites if not c.get("skipped")]
    citations_checked = sum(int(c.get("checked") or 0) for _, c in audited) if cites else None
    citation_examples = [
        {"ts": ts, "symbol": u.get("symbol"), "quoted": u.get("quoted"),
         "nearest": f"{(u.get('nearest') or {}).get('label')} {(u.get('nearest') or {}).get('value')}"}
        for ts, c in audited for u in (c.get("unsupported") or [])
    ]

    return {
        "decisions": len(decisions),
        "holds": holds,
        "proposals": proposals,
        "submitted": len(submitted),
        "submitted_entries": len(submitted) - len(exits),
        "submitted_exits": len(exits),
        "exit_reasons": dict(exit_reasons),
        "dry_run": len(dry),
        "rejected": len(rejected),
        "rejections_by_rule": dict(rej_by_rule.most_common(REJECTION_TOP_N)),
        "rejection_examples": {rule: rej_examples[rule] for rule, _ in rej_by_rule.most_common(REJECTION_TOP_N)},
        "rejections_by_symbol": dict(rej_by_symbol.most_common(REJECTION_TOP_N)),
        "errors": len(errors),
        "error_samples": [f"{r.get('where') or r.get('symbol')}: {str(r.get('detail'))[:120]}" for r in errors[:5]],
        "models": dict(models),
        "truncated_outputs": truncations,
        "tokens_in": usage_in,
        "tokens_out": usage_out,
        "latency_avg_sec": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "latency_max_sec": round(max(latencies), 2) if latencies else None,
        "citations_checked": citations_checked,
        "citations_unsupported": len(citation_examples) if cites else None,
        "citations_skipped_cycles": sum(1 for _, c in cites if c.get("skipped")) if cites else None,
        "citation_examples": citation_examples[:8],
    }


def config_changes(records: list[dict]) -> list[dict]:
    """Distinct effective configs seen today, in order - one line per
    change of config_hash, with the overrides that were active."""
    seen = []
    last = None
    for r in records:
        if r.get("event") != "config":
            continue
        h = r.get("config_hash")
        if h == last:
            continue
        last = h
        seen.append(
            {
                "ts": r.get("ts"),
                "config_hash": h,
                "config_file": r.get("config_file"),
                "model": r.get("model"),
                "overrides": sorted((r.get("overrides") or {}).keys()),
            }
        )
    return seen


def decisions_transcript(records: list[dict], max_chars: int = 400) -> list[dict]:
    """Every model decision of the day, trimmed, for skimming."""
    out = []
    for r in records:
        if r.get("event") != "decision":
            continue
        raw = str(r.get("raw") or "").strip()
        out.append({"ts": r.get("ts"), "count": r.get("count"), "model": r.get("model"),
                    "raw": raw[:max_chars] + ("..." if len(raw) > max_chars else "")})
    return out


def estimate_cost_usd(audit: dict, price_table: dict | None) -> float | None:
    """Rough $ from journaled tokens and a {model: {"in": $/M, "out": $/M}}
    table (config). None if no price known for any model used."""
    if not price_table or not audit.get("models"):
        return None
    # tokens are not split per model in the audit; approximate with the
    # most-used model's price (single-model days are the norm).
    model = max(audit["models"], key=audit["models"].get)
    p = price_table.get(model)
    if not p:
        return None
    return round(audit["tokens_in"] / 1e6 * float(p.get("in", 0)) + audit["tokens_out"] / 1e6 * float(p.get("out", 0)), 4)


def build_digest(day: str, account: str, records: list[dict], trade_summary: dict | None,
                 trips: list[dict] | None, price_table: dict | None = None) -> dict:
    audit = decision_audit(records)
    return {
        "date": day,
        "account": account,
        "generated_at": datetime.now(EASTERN).isoformat(timespec="seconds"),
        "equity": equity_facts(records),
        "trades": trade_summary or {},
        "trips": trips or [],
        "audit": audit,
        "cost_usd": estimate_cost_usd(audit, price_table),
        "config_changes": config_changes(records),
        "decisions": decisions_transcript(records),
        "halts": [r.get("event") for r in records if r.get("event") in ("daily_loss_halt", "manual_halt")],
    }


def render_markdown(d: dict) -> str:
    e, a, t = d["equity"], d["audit"], d.get("trades") or {}
    lines = [f"# EOD review - {d['date']} - account `{d['account']}`", ""]
    lines.append("## Equity")
    if e.get("equity_close") is not None:
        pnl = e.get("day_pnl")
        pct = e.get("day_pnl_pct")
        lines.append(f"- open {e['equity_open']:,.2f} -> close {e['equity_close']:,.2f}  "
                     f"(**{pnl:+,.2f}**, {pct:+.3f}%) over {e['cycles']} cycles")
    else:
        lines.append("- no cycles ran")
    if d.get("halts"):
        lines.append(f"- **halts today**: {', '.join(d['halts'])}")

    lines += ["", "## Trades (trade_report)"]
    if t.get("trades"):
        lines.append(f"- {t['trades']} round trips, net **{t['pnl']:+,.2f}**, win rate {t['win_rate_pct']}%, "
                     f"profit factor {t['profit_factor'] if t['profit_factor'] is not None else 'n/a'}, "
                     f"median hold {t['median_hold_min']}m")
        ex = {k: v['trades'] for k, v in t['by_exit_reason'].items() if v['trades']}
        lines.append(f"- exits: {ex}")
        for name, key in (("underlying", "by_underlying"), ("instrument", "by_instrument"), ("DTE at entry", "by_dte_at_entry")):
            cut = ", ".join(f"{k} {v['pnl']:+.0f} ({v['trades']})" for k, v in t[key].items())
            lines.append(f"- by {name}: {cut}")
    else:
        lines.append("- no completed round trips")

    lines += ["", "## Decisions"]
    lines.append(f"- {a['decisions']} decisions: {a['holds']} holds, {a['proposals']} proposals -> "
                 f"{a['submitted_entries']} entries submitted, {a['submitted_exits']} rule exits {a['exit_reasons'] or ''}, "
                 f"{a['rejected']} rejected, {a['errors']} errors, {a['dry_run']} dry-run")
    if a["rejections_by_rule"]:
        lines.append(f"- **rejections by rule**: {a['rejections_by_rule']}  (a rule rejecting the same idea all day is a prompt/config bug)")
        for rule, example in (a.get("rejection_examples") or {}).items():
            lines.append(f"  - `{rule}` in full: \"{example}\"")
        lines.append(f"- rejections by symbol: {a['rejections_by_symbol']}")
    if a["error_samples"]:
        lines.append("- errors: " + " | ".join(a["error_samples"]))
    if a.get("citations_checked") is not None:
        skipped = f", {a['citations_skipped_cycles']} cycle(s) skipped (research tools ran)" if a.get("citations_skipped_cycles") else ""
        lines.append(f"- **prior citations**: {a['citations_checked']} checked, {a['citations_unsupported']} unsupported{skipped}"
                     "  (a figure the model quoted that appears nowhere in the prior it was given - judge the decision on the journalled prior, not the quote)")
        for u in a.get("citation_examples") or []:
            lines.append(f"  - {str(u['ts'])[11:16]} {u['symbol']} quoted \"{u['quoted']}\" - nearest real number: {u['nearest']}")
    lines.append(f"- models: {a['models']}; {a['tokens_in']:,} in / {a['tokens_out']:,} out tokens; "
                 f"latency avg {a['latency_avg_sec']}s max {a['latency_max_sec']}s; truncated outputs {a['truncated_outputs']}"
                 + (f"; est. cost ${d['cost_usd']}" if d.get("cost_usd") is not None else ""))

    if d.get("prior_scores"):
        ps = d["prior_scores"]
        lines += ["", "## Prior scoring (Brier, lower is better; 0.25 = coin flip)"]
        if ps.get("skipped") or ps.get("error"):
            lines.append(f"- {ps.get('skipped') or ps.get('error')}")
        else:
            for source, today in (ps.get("today") or {}).items():
                run = (ps.get("running") or {}).get(source) or {}
                lines.append(f"- {source}: today {today}, running {run.get('mean')} over {run.get('days')} day(s)")
            withheld = ps.get("withheld") or []
            for source, mean in (ps.get("withheld_today") or {}).items():
                reasons = sorted({str(r.get("suppressed")) for r in withheld if r.get("source") == source})
                lines.append(f"- withheld {source}: today {mean} - shadow-graded, not in the means above "
                             f"(withheld for: {'; '.join(reasons)})")

    if d.get("config_changes"):
        lines += ["", "## Config seen today"]
        for c in d["config_changes"]:
            lines.append(f"- {c['ts']}: `{c['config_hash']}` {c['config_file']} model={c['model']} overrides={c['overrides'] or '-'}")

    if d.get("decisions"):
        lines += ["", "## Model output, every cycle"]
        for r in d["decisions"]:
            lines.append(f"- {str(r['ts'])[11:16]} ({r['count']}): `{r['raw']}`")

    if d.get("recommendation"):
        # Name the reviewer: the docs promise it is not the model that traded,
        # and for two days it silently was (#177). A claim in the header is
        # one a reader can check against the config block above.
        by = f", by {d['review_model']}" if d.get("review_model") else ""
        lines += ["", f"## Model's read of the day (advisory{by})", "", d["recommendation"]]
    return "\n".join(lines) + "\n"

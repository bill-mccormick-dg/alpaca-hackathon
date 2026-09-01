"""Does the model's stated reason quote numbers it was actually given? (#172)

On 2026-09-01 the judged account's model cited "68.7% chance of down>1%
close" (the prompt said 33.8%), "only 7.6% chance of finishing above prior
close" (12.6%) and "extreme bearishness (81.9% down>1%)" (59.5%; the chain
said 43.7%). Three citations, three wrong, all skewed toward the trade
being proposed. The prior was fetched, rendered and journaled correctly -
this is not a data-path bug - and research tools were off, so the prompt
was the only source. Those reason strings are load-bearing downstream:
the EOD digest, the reviewer model's critique, the hourly email, the
public feed and the writeup all quote them as fact.

The check is cheap because both sides are already structured: the prior
is in memory in run_cycle as predictions.journal_fields(...) and the
reasons are on the proposals. A percentage-shaped token in a reason, in a
clause that is talking about the prior, is looked up against every number
the prior block carried (and its complement, for "P(below)" phrasing);
anything that matches nothing within half a point is unsupported. The
result rides on the `decision` journal event - measurable, not anecdotal -
and the digest and reviewer read it from there.

Deliberately reporting only. The funnel keeps bounding what can be traded;
prose is not an order parameter, and check_order does not grade rhetoric.
"""

import re

from bot.models import Proposal

PRIOR_FIELDS = (("p_above_reference", "P(above)"), ("p_up_over_1pct", "P(up>1%)"), ("p_down_over_1pct", "P(down>1%)"))

# A clause is "about the prior" when it names a crowd, a probability, or the
# block's own labels. Deltas are excluded: "delta 0.48 ~ 48% chance ITM" is a
# menu number, not a prior, and would be a false positive.
CONTEXT = re.compile(r"kalshi|prior|crowd|prediction|implied odds|option chain|chain-implied|market-implied|"
                     r"chance|probabilit|odds|\bP\(", re.IGNORECASE)
EXCLUDE = re.compile(r"delta", re.IGNORECASE)
PERCENT = re.compile(r"(?<![\w.<>])(\d{1,3}(?:\.\d+)?)\s?%")
FRACTION = re.compile(r"(?<![\w.])(0\.\d{2,3})(?![\d%])")
TOLERANCE = 0.006  # half a percentage point, plus the block's own rounding


def prior_values(prior: dict | None) -> list[tuple[str, float]]:
    """Every number the prior block could have shown, labelled, from a
    journal-shaped predictions dict (the `predictions` event, or
    journal_fields()). Withheld sources are skipped - the model never saw
    them. Complements are included so "P(below) 91%" of P(above) 0.09 is
    supported."""
    out: list[tuple[str, float]] = []
    for underlying, entry in (prior or {}).items():
        if underlying in ("ts", "event", "account") or not isinstance(entry, dict):
            continue
        sources = [("Kalshi", entry)]
        if isinstance(entry.get("chain"), dict):
            sources.append(("chain", entry["chain"]))
        for label, src in sources:
            if src.get("suppressed"):
                continue
            for key, name in PRIOR_FIELDS:
                v = src.get(key)
                if isinstance(v, (int, float)):
                    out.append((f"{underlying} {label} {name}", float(v)))
                    out.append((f"1 - {underlying} {label} {name}", 1.0 - float(v)))
            move = src.get("implied_move_pct")
            if isinstance(move, (int, float)):
                out.append((f"{underlying} {label} implied move", abs(float(move)) / 100.0))
    return out


def extract_claims(reason: str) -> list[tuple[str, float]]:
    """(as written, as a fraction) for every percentage or 0.xx fraction in a
    clause about the prior. "+32% vs entry" and "down 1.2% intraday" are in
    clauses about price, and are not claims."""
    claims = []
    for clause in re.split(r"[;,]|\.(?!\d)", reason or ""):
        if not CONTEXT.search(clause) or EXCLUDE.search(clause):
            continue
        for m in PERCENT.finditer(clause):
            claims.append((m.group(0).strip(), round(float(m.group(1)) / 100.0, 4)))
        for m in FRACTION.finditer(clause):
            claims.append((m.group(1), float(m.group(1))))
    return claims


def audit(proposals: list[Proposal], prior: dict | None, tool_calls=None, tol: float = TOLERANCE) -> dict | None:
    """None when no prior was shown (nothing to audit against). Skipped when
    research tools ran, since a quoted figure may then come from a tool
    result the audit cannot see. Otherwise {"checked", "unsupported": [...]}."""
    values = prior_values(prior)
    if not values:
        return None
    if tool_calls:
        return {"skipped": "research tools ran - a quoted figure may come from a tool result"}
    checked, unsupported = 0, []
    for p in proposals:
        for quoted, value in extract_claims(p.reason):
            checked += 1
            label, nearest = min(values, key=lambda lv: abs(lv[1] - value))
            if abs(nearest - value) > tol:
                unsupported.append({"symbol": p.symbol, "quoted": quoted,
                                    "nearest": {"label": label, "value": round(nearest, 3)}})
    return {"checked": checked, "unsupported": unsupported}


def describe(result: dict | None) -> str:
    """One line for the terminal / digest."""
    if not result:
        return "prior citations: nothing to audit"
    if result.get("skipped"):
        return f"prior citations: skipped ({result['skipped']})"
    n = len(result.get("unsupported") or [])
    head = f"prior citations: {result.get('checked', 0)} checked, {n} unsupported"
    if not n:
        return head
    bits = [f"{u['symbol']} quoted {u['quoted']} (nearest {u['nearest']['label']} {u['nearest']['value']})" for u in result["unsupported"]]
    return head + " - " + "; ".join(bits)

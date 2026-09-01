"""Does the model's stated reason quote numbers it was actually given? (#172)

Built on a suspicion that turned out to be the operator's error, and kept
because the check is cheap and the risk is real. On 2026-09-01 the judged
account's model wrote "68.7% chance of down>1% close", "only 7.6% chance
of finishing above prior close" and "extreme bearishness (81.9% down>1%)".
Read against the prompt an hour earlier those looked invented; read
against the prior journaled in the SAME cycle (13:00 and 13:20 Eastern -
the email and the viewer show Central time, which is how the hour slipped)
all three are exact. The first run of this audit over that day found 22
quoted figures, 22 supported. What it did surface was subtler: at 13:20
the QQQ chain prior was withheld and the model quoted SPY's chain figure
as "the options market" for QQQ - a real number, attributed to the wrong
underlying.

So the audit reports two things. `unsupported`: a figure in a prior-shaped
clause that matches nothing the prior block carried (either crowd, either
underlying, or a complement for "P(below)" phrasing) within half a point.
`misattributed`: a figure that matches only another underlying's prior
when the proposal's own underlying had a prior on show. Both ride on the
`decision` journal event - measurable, not anecdotal - and the digest and
the reviewer read them from there. The reason strings are load-bearing
downstream (digest, reviewer, email, feed, writeup), which is why this is
worth a few lines per cycle even on a day the model quoted honestly.

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


def _underlying_of(label: str) -> str:
    return label.removeprefix("1 - ").split(" ")[0]


def audit(proposals: list[Proposal], prior: dict | None, tool_calls=None, tol: float = TOLERANCE) -> dict | None:
    """None when no prior was shown (nothing to audit against). Skipped when
    research tools ran, since a quoted figure may then come from a tool
    result the audit cannot see. Otherwise {"checked", "unsupported": [...],
    "misattributed": [...]} - see the module docstring for the two kinds."""
    values = prior_values(prior)
    if not values:
        return None
    if tool_calls:
        return {"skipped": "research tools ran - a quoted figure may come from a tool result"}
    checked, unsupported, misattributed = 0, [], []
    for p in proposals:
        own = p.underlying or ""
        own_values = [lv for lv in values if _underlying_of(lv[0]) == own]
        for quoted, value in extract_claims(p.reason):
            checked += 1
            label, nearest = min(values, key=lambda lv: abs(lv[1] - value))
            entry = {"symbol": p.symbol, "quoted": quoted, "nearest": {"label": label, "value": round(nearest, 3)}}
            if abs(nearest - value) > tol:
                unsupported.append(entry)
            elif own_values and _underlying_of(label) != own and not any(abs(v - value) <= tol for _, v in own_values):
                # Supported by another name's prior only, while this name had
                # one on show: the 13:20 case - SPY's chain quoted for QQQ.
                misattributed.append(entry)
    return {"checked": checked, "unsupported": unsupported, "misattributed": misattributed}


def describe(result: dict | None) -> str:
    """One line for the terminal / digest."""
    if not result:
        return "prior citations: nothing to audit"
    if result.get("skipped"):
        return f"prior citations: skipped ({result['skipped']})"
    n, m = len(result.get("unsupported") or []), len(result.get("misattributed") or [])
    head = f"prior citations: {result.get('checked', 0)} checked, {n} unsupported, {m} misattributed"
    if not n and not m:
        return head
    bits = [f"{u['symbol']} quoted {u['quoted']} (nearest {u['nearest']['label']} {u['nearest']['value']})" for u in result["unsupported"]]
    bits += [f"{u['symbol']} quoted {u['quoted']} which is {u['nearest']['label']}" for u in result.get("misattributed") or []]
    return head + " - " + "; ".join(bits)

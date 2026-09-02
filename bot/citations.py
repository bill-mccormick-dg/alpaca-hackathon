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

Since #188 (found by godmagick reading the fills, not the journal) the
audit also grades EXIT reasons against facts the account can contradict.
On 2026-09-01 the judged account sold a 7-DTE SPY put at -12% across four
attempts; every reason cited expiry pressure that did not exist ("forced
expiry sale", "forced close tomorrow", "backstop forces exit" - with
expiry_close_dte=0 / eod_close_dte=1 no code path touches a 7-DTE contract
for six more days), and the filled exit claimed the "market held above
prior close" while SPY sat 0.73% BELOW it - the strike (760) conflated
with the prior close (766.87). Meanwhile the evidence actually cited at
entry had strengthened (Kalshi P(above prior) 0.11 -> 0.074). Two checks
follow from that trade: `fabricated_urgency`, a sell citing a forced
close/backstop when the contract's DTE puts it days beyond any code
action; and `wrong_direction`, an above/below-prior-close claim the tape
contradicts. Same posture as the prior audit: journal and digest only.

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


# A sell reason claiming an imminent code-driven exit. Every one of #188's
# four exit attempts matches: "forced expiry sale", "forced close tomorrow",
# "forced close approaches", "backstop forces exit". Plain DTE/theta talk is
# deliberately NOT matched - "theta decay accelerating" at 7 DTE is arguable
# rhetoric, but a forced close that is days away is a checkable falsehood.
EXPIRY_URGENCY = re.compile(r"forced\s+(?:close|exit|sale|expiry)|expiry\s+sale|backstop", re.IGNORECASE)

# "market held above prior close" and kin - a direction claim measured
# against a number the snapshot carries. A verb OR a price must precede
# above/below: "227.55, below the prior close" is a claim (#226), while the
# prior block's own label "P(above prior close) 0.571" is not, and the
# prefix is what keeps the label from matching. An optional "of 228.00"
# captures a STATED prior close, checked against the real one.
DIRECTION_CLAIM = re.compile(
    r"(?:(?:held|holding|holds|stayed|staying|remains?|remained|is|was|closed?|trading|now|sits?|sitting)\s+"
    r"(?:(?:at\s+)?\$?\d[\d,]*\.?\d*,?\s+)?|\$?\d[\d,]*\.\d+,?\s+)"
    r"(above|below)\s+(?:the\s+|its\s+|yesterday'?s\s+)?prior\s+close"
    r"(?:\s+(?:of|at)\s+\$?(\d[\d,]*\.?\d*))?", re.IGNORECASE)

# A stated prior close is wrong when it is off by more than this fraction of
# the real one - wide enough to forgive rounding to the dollar, narrow enough
# that 228.00 for 217.49 (4.8%) is caught.
REFERENCE_TOLERANCE = 0.005


def audit_exit_claims(proposals: list[Proposal], dte_by_symbol: dict[str, int],
                      spot_ref: dict[str, tuple[float, float]], config: dict) -> list[dict]:
    """Facts in a reason that the account itself contradicts (#188).

    `dte_by_symbol` is DTE per held option symbol; `spot_ref` is
    {underlying: (spot, prior_close)} in the underlying's own terms. Two
    kinds of flag, each with the quoted phrase and the contradicting fact:

    - fabricated_urgency: a SELL citing a forced close / backstop when the
      contract sits beyond eod_close_dte + 1 - no code path can touch it
      today or tomorrow, so the urgency is invented.
    - wrong_direction: any proposal claiming the underlying is above/below
      its prior close when the snapshot's spot says the opposite.
    - wrong_reference: a stated prior close ("below the prior close of
      228.00") that is not the real one - the 2026-09-02 NVDA exit cited
      228.00 against a real 217.49, and the direction happened to be right.
    """
    horizon = max(int(config.get("eod_close_dte", 1)), int(config.get("expiry_close_dte", 0)))
    flags = []
    for p in proposals:
        reason = p.reason or ""
        if p.side == "sell":
            dte = dte_by_symbol.get(p.symbol)
            if dte is not None and dte > horizon + 1:
                m = EXPIRY_URGENCY.search(reason)
                if m:
                    flags.append({"symbol": p.symbol, "kind": "fabricated_urgency", "quoted": m.group(0),
                                  "fact": f"{dte} DTE - no code exit for {dte - horizon} more day(s)"})
        pair = spot_ref.get(p.whitelist_symbol)
        if pair:
            spot, ref = pair
            m = DIRECTION_CLAIM.search(reason)
            if m and ref and m.group(2):
                stated = _number(m.group(2))
                if stated is not None and abs(stated - ref) > REFERENCE_TOLERANCE * ref:
                    flags.append({"symbol": p.symbol, "kind": "wrong_reference", "quoted": m.group(0),
                                  "fact": f"{p.whitelist_symbol} prior close is {ref:g}, not {stated:g}"})
            if m and spot and ref and spot != ref:
                actually = "above" if spot > ref else "below"
                if m.group(1).lower() != actually:
                    flags.append({"symbol": p.symbol, "kind": "wrong_direction", "quoted": m.group(0),
                                  "fact": f"{p.whitelist_symbol} {spot:g} is {actually} prior close {ref:g}"})
    return flags


def _number(text: str) -> float | None:
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def describe_exit_claims(flags: list[dict]) -> str:
    """One line for the terminal / digest."""
    bits = [f"{f['symbol']} said \"{f['quoted']}\" but {f['fact']}" for f in flags]
    return f"exit claims contradicted by the account: {len(flags)} - " + "; ".join(bits)


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

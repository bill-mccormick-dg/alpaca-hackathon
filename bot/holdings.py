"""What the model knows about its own positions (#170, #173).

Every cycle is a fresh process. Until this module the model saw that a
position existed and how it was doing - one row in the snapshot JSON - and
nothing else: not why it was opened, not what the prior said at the time,
not even a plain list of what it is allowed to sell. Two live failures on
the judged account on 2026-09-01 came straight from that:

  #170  it tried to close a +32% SPY put three cycles running and named a
        neighbouring strike each time (763, 763, 762 for a held 764). The
        held symbol was one row among thirty in the JSON, and since #158 the
        menu lists its same-expiry neighbours right beside it.
  #173  it proposed exiting a ten-minute-old QQQ put on a "weakening thesis"
        while every number it had cited at entry had moved in its favour.
        It re-derived the view from scratch, because the entry view was
        nowhere in the prompt.

So: a prose block naming the held symbols as the ONLY legal sell targets,
each with the reason journaled when it was opened, the prior in force
then, and the prior now; and, for the one operation where the symbol is
already known, a resolver that maps an unambiguous neighbouring-strike
sell onto the contract actually held.

The entry prior is not garnish. Per #172 the model fabricates statistics
inside its reasons, so replaying a reason alone would let it "verify" a
position against its own confabulation. Pairing the reason with the
journaled prior turns "has my thesis weakened?" into a comparison against
numbers we control.

Each position also states its code exits and the first date an expiry rule
can touch it (#188, found by godmagick reading the fills): on 2026-09-01
the judged account sold a 7-DTE SPY put at -12% across four attempts whose
reasons all cited expiry pressure that did not exist - "forced expiry
sale", "forced close tomorrow", "backstop forces exit" - when with
expiry_close_dte=0 / eod_close_dte=1 no code path would have touched the
contract for six more days. The prompt warned about buying NEAR the
backstop and the model generalized that into ambient expiry anxiety at any
DTE. The treatment is the same as for the priors: state the fact per
position, so "the forced close approaches" has a printed date to be wrong
against.

Pure functions; run_cycle.py reads the journal and passes records in.
"""

from dataclasses import replace
from datetime import datetime, timedelta

from bot.models import Position, Proposal
from bot.occ import parse_occ_symbol
from bot.risk import EASTERN

# Journal field -> prompt label, in the order they print. Same keys at the
# Kalshi (top) level and under ["chain"] - bot/predictions.py::journal_fields.
PRIOR_FIELDS = (("p_above_reference", "P(above)"), ("p_up_over_1pct", "P(up>1%)"), ("p_down_over_1pct", "P(down>1%)"))
SOURCES = (("kalshi", "Kalshi"), ("chain", "chain"))

NO_THESIS = "no recorded thesis (opened before the journal, or restored)"


def _hhmm(ts) -> str:
    """HH:MM Eastern from a journal (Eastern, offset-bearing) or broker (UTC
    'Z') timestamp; the raw head of the string if it will not parse."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(ts or "?")[11:16] or "?"
    if dt.tzinfo is not None:
        dt = dt.astimezone(EASTERN)
    return dt.strftime("%H:%M")


def _underlying_of(symbol: str) -> str | None:
    try:
        return parse_occ_symbol(symbol).underlying
    except ValueError:
        return None


def prior_for(record: dict | None, underlying: str | None) -> dict | None:
    """One underlying's prior out of a journal-shaped predictions dict (the
    `predictions` event, or predictions.journal_fields()): {"kalshi": {...},
    "chain": {...}}, either absent. None when there is nothing for it."""
    if not record or not underlying:
        return None
    entry = record.get(underlying)
    if not isinstance(entry, dict):
        return None
    out = {}
    if entry.get("series") or any(entry.get(k) is not None for k, _ in PRIOR_FIELDS):
        out["kalshi"] = {k: entry.get(k) for k, _ in PRIOR_FIELDS} | {"suppressed": entry.get("suppressed")}
    chain = entry.get("chain")
    if isinstance(chain, dict):
        out["chain"] = {k: chain.get(k) for k, _ in PRIOR_FIELDS} | {"suppressed": chain.get("suppressed")}
    return out or None


def _fmt_prior(prior: dict | None) -> str:
    if not prior:
        return "none"
    parts = []
    for key, label in SOURCES:
        src = prior.get(key)
        if not src:
            continue
        if src.get("suppressed"):
            parts.append(f"{label} withheld ({src['suppressed']})")
            continue
        nums = [f"{name} {src[k]:.3f}" for k, name in PRIOR_FIELDS if isinstance(src.get(k), (int, float))]
        if nums:
            parts.append(f"{label} " + ", ".join(nums))
    return " | ".join(parts) or "none"


def _flattened(record: dict) -> set[str]:
    """Symbols a `flatten` / `daily_loss_flatten` event says it closed."""
    out = set()
    for item in record.get("closed") or []:
        sym = item.get("symbol") if isinstance(item, dict) else item
        if sym:
            out.add(str(sym))
    return out


def code_exit_line(symbol: str, instrument: str, config: dict | None, today) -> str | None:
    """The code exits that own this position, with the first date an expiry
    rule can act, so expiry pressure is a printed fact rather than a feeling
    (#188). None when config or today is absent (older callers, tests)."""
    if not config or today is None:
        return None
    stop = float(config.get("stop_loss_pct", 40))
    tp = float(config.get("take_profit_pct", 60))
    head = f"code exits: stop-loss -{stop:.0f}% / take-profit +{tp:.0f}% of entry"
    if instrument != "option":
        return head + "; shares have no expiry, so no DTE rule applies"
    try:
        occ = parse_occ_symbol(symbol)
    except ValueError:
        return head
    dte = (occ.expiration - today).days
    expiry_close = int(config.get("expiry_close_dte", 0))
    eod = int(config.get("eod_close_dte", 1))
    if dte <= expiry_close:
        return head + f", and code closes this contract TODAY ({dte} DTE)"
    if dte <= eod:
        return head + f", and the end-of-day backstop closes this contract TODAY ({dte} DTE)"
    first = occ.expiration - timedelta(days=max(eod, expiry_close))
    return (head + f"; NO expiry-driven exit before {first.isoformat()} ({dte} DTE today) - until "
            'then DTE, theta, or a "forced close" are not exit reasons')


def entry_context(positions: list[dict], records: list[dict]) -> dict[str, dict | None]:
    """Per held symbol: when and why it was opened and the prior in force
    then, or None when the journal has no opener for it.

    Walks the journal in order keeping a running quantity per symbol, so the
    "opener" is the first buy since the position was last flat - a symbol
    traded, closed and re-bought gets today's reason, not last week's. The
    prior at entry is the last `predictions` record BEFORE the opener in
    journal order (predictions is written before cycle_start, so that is the
    same cycle's prior). Later buys of the same symbol count as adds."""
    held = {p["symbol"]: p.get("underlying") or _underlying_of(p["symbol"]) for p in positions}
    out: dict[str, dict | None] = dict.fromkeys(held)
    qty: dict[str, float] = dict.fromkeys(held, 0.0)
    last_prior = None
    for r in records:
        ev = r.get("event")
        if ev == "predictions":
            last_prior = r
            continue
        if ev in ("flatten", "daily_loss_flatten"):
            for sym in _flattened(r) & held.keys():
                out[sym], qty[sym] = None, 0.0
            continue
        if ev != "order_submitted":
            continue
        sym = r.get("symbol")
        if sym not in held:
            continue
        n = float(r.get("qty") or 0)
        if r.get("side") == "sell":
            qty[sym] -= n
            if qty[sym] <= 0:
                out[sym], qty[sym] = None, 0.0
            continue
        if r.get("side") != "buy":
            continue
        if out[sym] is None:
            out[sym] = {
                "opened_ts": r.get("ts"),
                "reason": str(r.get("reason") or ""),
                "adds": 0,
                "prior_at_entry": prior_for(last_prior, held[sym]),
            }
        else:
            out[sym]["adds"] += 1
        qty[sym] += n
    return out


def render_positions_block(positions: list[dict], open_orders: list[dict] | None, context: dict,
                           prior_now: dict | None, config: dict | None = None, today=None) -> str:
    """The prompt block. `positions` and `open_orders` are the snapshot's
    lists; `context` is entry_context(); `prior_now` is journal-shaped
    (predictions.journal_fields of this cycle's snapshot). `config` and
    `today` enable the per-position code-exits line (#188)."""
    lines = []
    if not positions:
        lines.append("POSITIONS YOU HOLD: none - any sell would be a naked short and is refused.")
    else:
        lines.append("POSITIONS YOU HOLD (the ONLY symbols a sell may name - copy them exactly):")
        for p in positions:
            sym = p["symbol"]
            entry, cur = p.get("avg_entry_price"), p.get("current_price")
            move = f", {((cur / entry) - 1) * 100:+.1f}% vs entry" if entry and cur else ""
            head = f"- {sym} x{float(p.get('qty') or 0):.0f} @ {entry if entry is not None else '?'}{move}"
            exits = code_exit_line(sym, p.get("instrument") or "option", config, today)
            ctx = context.get(sym)
            if not ctx:
                lines.append(f"{head}; {NO_THESIS}")
                if exits:
                    lines.append(f"    {exits}")
                continue
            adds = ctx.get("adds") or 0
            added = f" (+{adds} add{'s' if adds != 1 else ''} since)" if adds else ""
            lines.append(f"{head}; opened {_hhmm(ctx.get('opened_ts'))} ET{added}")
            lines.append(f'    stated at entry: "{ctx.get("reason") or "(no reason given)"}"')
            underlying = p.get("underlying") or _underlying_of(sym)
            then, now = ctx.get("prior_at_entry"), prior_for(prior_now, underlying)
            if then or now:
                lines.append(f"    prior at entry: {_fmt_prior(then)}")
                lines.append(f"    prior now:      {_fmt_prior(now)}")
            if exits:
                lines.append(f"    {exits}")
    if open_orders is None:
        lines.append("RESTING ORDERS: unknown this cycle (the open-order lookup failed) - "
                     "a buy you sent last cycle may still be working; do not send it again.")
    elif open_orders:
        lines.append("RESTING ORDERS (sent, unfilled - they already count against your caps; "
                     "a resting buy is not a position yet, and must not be bought again):")
        for o in open_orders:
            price = f" @ limit {o['limit_price']}" if o.get("limit_price") is not None else " at market"
            lines.append(f"- {o.get('side')} {float(o.get('qty') or 0):.0f} {o.get('symbol')}{price}, "
                         f"submitted {_hhmm(o.get('submitted_at'))} ET")
    return "\n".join(lines) + "\n\n"


def resolve_sell(p: Proposal, positions: dict[str, Position]) -> tuple[Proposal, str | None]:
    """A sell of an option not held, where exactly one held contract has the
    same underlying, type and expiration, becomes a sell of that contract -
    the symbol differs only in the strike, and that is the #170 failure
    exactly. Anything else is returned untouched: buys, stock, a symbol
    already held, no match, or more than one (two same-expiry puts held is
    a real ambiguity, and the funnel's rejection then names both).

    Only ever reduces exposure - a sell can only close something held, and
    check_order still bounds the quantity - so a wrong guess costs at worst
    an exit the model did not mean, never a position it did not want."""
    if p.side != "sell" or p.instrument != "option" or p.symbol in positions:
        return p, None
    try:
        occ = parse_occ_symbol(p.symbol)
    except ValueError:
        return p, None
    matches = []
    for symbol, pos in positions.items():
        if pos.instrument != "option":
            continue
        try:
            held = parse_occ_symbol(symbol)
        except ValueError:
            continue
        if (held.underlying, held.option_type, held.expiration) == (occ.underlying, occ.option_type, occ.expiration):
            matches.append(symbol)
    if len(matches) != 1:
        return p, None
    note = (f"resolved {p.symbol} -> {matches[0]} (the one {occ.underlying} {occ.option_type} held for "
            f"{occ.expiration.isoformat()}; only the strike differed)")
    return replace(p, symbol=matches[0], underlying=occ.underlying), note


def held_on_same_underlying(p: Proposal, positions: dict[str, Position]) -> list[str]:
    """'SPY260908P00764000 x10' for every option held on the proposal's
    underlying - what a 'cannot sell, only 0 held' rejection should name so
    the next cycle's learning block carries the right symbol."""
    underlying = p.underlying or _underlying_of(p.symbol)
    out = []
    for symbol, pos in positions.items():
        if pos.instrument == "option" and (pos.underlying or _underlying_of(symbol)) == underlying:
            out.append(f"{symbol} x{pos.qty:.0f}")
    return out

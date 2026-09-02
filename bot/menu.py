"""Which contracts make the model's menu (#159).

The chain fetch (bot/snapshot.py) brings back every contract in the strike
band across the whole DTE window - thousands on SPY. The prompt shows
research_contracts_per_underlying of them (12). Until #159 those were the
12 nearest the money by |strike - spot|, then DTE, and on names with coarse
strikes that collapsed to ONE strike: NVDA's 12 on 2026-08-31 were all
K=220, call and put across six expiries; MSFT's all 510; AAPL 10 of 12 at
317.5. The model could choose side and expiry from the menu and nothing
else - "half a delta out" was not on offer - and its NVDA flip-flopping
(call, put, call, all at 220) was exactly the degenerate choice space the
menu gave it. SPY and QQQ escaped only by the accident of $1 strikes.

The tactics ask for "at or slightly out of the money (|delta| roughly
0.35-0.55)" with 2-14 days to expiration. So the menu now spends its slots
on that: three expiry buckets across the tactics' band, and per expiry and
side the at-the-money strike plus the out-of-the-money strike whose delta
is nearest MENU_OTM_DELTA (Alpaca's own delta, #160; the next strike out
when a contract has none). ATM picks are taken for every bucket before any
OTM pick, so a small slot count still spans expiries. Leftover slots fall
back to the old nearest-the-money rule, and the result is ordered by
expiry then strike so the prompt reads as a grid.

Constants, not knobs: they shape what the model sees, the slot count is
already a knob, and the selection rule is code the config hash does not
need to track. Pure and deterministic - the same chain gives the same menu
regardless of dict order.
"""

from datetime import date

from bot.occ import parse_occ_symbol

MENU_DTE_TARGETS = (2, 7, 14)   # the tactics' 2-14 DTE band: near, middle, far
MENU_OTM_DELTA = 0.40           # "slightly out of the money": the band's centre


def _delta(raw: dict) -> float | None:
    greeks = raw.get("greeks") if isinstance(raw, dict) else None
    if not isinstance(greeks, dict) or greeks.get("delta") is None:
        return None
    try:
        return abs(float(greeks["delta"]))
    except (TypeError, ValueError):
        return None


def parse_pool(contracts: dict, today: date, min_dte: int = 1) -> list[dict]:
    """The tradeable pool: parseable OCC symbols, not expired, not below the
    entry floor (the funnel would refuse the buy, so the menu must not
    offer it). Sorted by symbol so nothing downstream depends on dict order."""
    pool = []
    for symbol in sorted(contracts or {}):
        try:
            occ = parse_occ_symbol(symbol)
        except ValueError:
            continue
        dte = (occ.expiration - today).days
        if dte <= 0 or dte < min_dte:
            continue
        raw = contracts[symbol]
        pool.append({"symbol": symbol, "strike": occ.strike, "type": occ.option_type, "dte": dte,
                     "expiration": occ.expiration, "delta": _delta(raw), "raw": raw})
    return pool


def expiry_buckets(pool: list[dict]) -> list[date]:
    """The distinct expiry nearest each MENU_DTE_TARGET, deduplicated in
    target order. Fewer than three when the chain has fewer expiries."""
    by_dte = {}
    for c in pool:
        by_dte.setdefault(c["expiration"], c["dte"])
    out: list[date] = []
    for target in MENU_DTE_TARGETS:
        best = min(by_dte, key=lambda exp: (abs(by_dte[exp] - target), by_dte[exp]))
        if best not in out:
            out.append(best)
    return out


def select_menu(contracts: dict, spot: float, today: date, limit: int, min_dte: int = 1) -> list[tuple[str, dict]]:
    """[(symbol, raw)] for the prompt, at most `limit`, ordered by expiry,
    strike, type."""
    pool = parse_pool(contracts, today, min_dte)
    if not pool or limit <= 0 or not spot:
        return []
    chosen: list[dict] = []
    taken: set[str] = set()

    def take(c: dict | None) -> None:
        if c is not None and c["symbol"] not in taken and len(chosen) < limit:
            taken.add(c["symbol"])
            chosen.append(c)

    buckets = expiry_buckets(pool)
    side_of = {}
    for c in pool:
        side_of.setdefault((c["expiration"], c["type"]), []).append(c)

    # 1. At the money, every bucket, both sides.
    for exp in buckets:
        for side in ("call", "put"):
            cs = side_of.get((exp, side))
            if cs:
                take(min(cs, key=lambda c: (abs(c["strike"] - spot), c["strike"])))

    # 2. Slightly out of the money: by delta when Alpaca priced it, else the
    #    next strike out.
    for exp in buckets:
        for side in ("call", "put"):
            otm = [c for c in side_of.get((exp, side)) or []
                   if c["symbol"] not in taken and (c["strike"] > spot if side == "call" else c["strike"] < spot)]
            if not otm:
                continue
            priced = [c for c in otm if c["delta"] is not None]
            if priced:
                take(min(priced, key=lambda c: (abs(c["delta"] - MENU_OTM_DELTA), abs(c["strike"] - spot))))
            else:
                take(min(otm, key=lambda c: abs(c["strike"] - spot)))

    # 3. Whatever is left: nearest the money, shortest DTE first (the pre-#159 rule).
    for c in sorted(pool, key=lambda c: (abs(c["strike"] - spot), c["dte"])):
        if len(chosen) >= limit:
            break
        take(c)

    chosen.sort(key=lambda c: (c["dte"], c["strike"], c["type"]))
    return [(c["symbol"], c["raw"]) for c in chosen]

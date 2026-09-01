"""Builds the state the decision loop and risk checks consume each cycle.

Two halves: account/position state (deterministic — "what do we currently
hold") and market/options research ("what's out there"). Response shapes
here are Alpaca's own REST/market-data objects, which the MCP server
proxies close to 1:1 (confirmed against live tool output, not just docs) —
account/position fields follow
https://docs.alpaca.markets/reference/getaccount and
https://docs.alpaca.markets/reference/getallopenpositions.
"""

import json
from datetime import date, datetime, timedelta

from bot.alpaca_mcp import AlpacaMCPClient
from bot.models import AccountState, Position, Proposal
from bot.occ import parse_occ_symbol
from bot.risk import EASTERN

# Option chain paging (issue #158). Alpaca returns the chain ordered by
# expiration and pages it; on SPY/QQQ ($1 strikes, an expiry every weekday)
# an 8% band is ~245 contracts per expiration, so one page covered 1-3 DTE
# of a 45-day window. CHAIN_PAGE_LIMIT is the API maximum. CHAIN_MAX_PAGES
# is a latency/memory safety cap, not a strategy knob - SPY's full window is
# ~8 pages - and hitting it is reported as `truncated` in the research dict
# and journaled on cycle_start rather than silently shortening the menu.
CHAIN_PAGE_LIMIT = 1000
CHAIN_MAX_PAGES = 12


def _data(result) -> dict:
    """Unwrap an MCP tool_call result's {"data": ...} envelope.

    The server reports upstream failures as a plain-text result ("Error
    calling tool 'x': ...") rather than raising, so a non-JSON body is an
    Alpaca-side failure - surface that text instead of a JSONDecodeError
    that hides it."""
    text = "\n".join(block.text for block in getattr(result, "content", []) if hasattr(block, "text"))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"Alpaca MCP tool failed: {text[:300]}") from None
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def _optional_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def build_positions(client: AlpacaMCPClient) -> dict:
    result = await client.call_tool("get_all_positions")
    data = _data(result)
    raw_positions = data.get("result", data) if isinstance(data, dict) else data

    positions = {}
    for raw in raw_positions:
        symbol = raw["symbol"]
        is_option = raw.get("asset_class") == "us_option"
        underlying = None
        if is_option:
            try:
                underlying = parse_occ_symbol(symbol).underlying
            except ValueError:
                underlying = None  # unexpected symbol shape; leave for risk.py to reject downstream

        # abs(): this project is long-only (no proposal path ever opens a
        # short — see risk.py's sell check, which rejects selling more than
        # is held starting from a flat position). Position qty/value are
        # treated as plain magnitudes throughout the codebase.
        positions[symbol] = Position(
            symbol=symbol,
            instrument="option" if is_option else "stock",
            qty=abs(float(raw["qty"])),
            market_value=abs(float(raw.get("market_value", 0))),
            underlying=underlying,
            avg_entry_price=_optional_float(raw.get("avg_entry_price")),
            current_price=_optional_float(raw.get("current_price")),
        )
    return positions


async def build_account_state(client: AlpacaMCPClient) -> AccountState:
    result = await client.call_tool("get_account_info")
    data = _data(result)
    return AccountState(
        equity=float(data["equity"]),
        # Alpaca's last_equity = equity as of the prior trading day's close
        # — exactly today's start-of-day equity.
        start_of_day_equity=float(data["last_equity"]),
        cash=float(data["cash"]),
        positions=await build_positions(client),
        account_number=data.get("account_number"),
    )


async def fetch_account_number(client: AlpacaMCPClient) -> str | None:
    """Just the broker's account number, for entrypoints that verify identity
    without building a whole snapshot (flatten.py). None if it isn't there."""
    return _data(await client.call_tool("get_account_info")).get("account_number")


async def quote_option_mid(client: AlpacaMCPClient, symbol: str) -> float | None:
    """Bid/ask mid for ONE option contract, from a live quote - the pricing
    path for a proposal the snapshot cannot price.

    Until #158 the snapshot's chain was one API page (500 contracts), and for
    SPY inside an 8% strike band that was three expiries, whatever the
    configured DTE window said. A model with research tools could look at a
    fourth expiry, propose it with a perfectly good limit price, and have the
    funnel reject it as "price must be positive" - which is what happened
    twice on 2026-08-31 to a 4-DTE SPY put. The chain is paginated now, so
    this is the fallback for a contract outside the fetched band or window
    (or beyond the page cap), and it closes that gap without loosening
    anything: a real quote still has to exist, so an invented symbol is
    rejected exactly as before, and the price used for sizing is the
    market's, not the model's.

    None on a missing quote, a one-sided quote, or any tool failure; the
    caller treats None as unpriceable."""
    try:
        result = await client.call_tool("get_option_latest_quote", {"symbols": symbol, "feed": "indicative"})
        quote = (_data(result).get("quotes") or {}).get(symbol) or {}
    except Exception:  # noqa: BLE001 - unpriceable is the safe answer
        return None
    bid, ask = _optional_float(quote.get("bp")), _optional_float(quote.get("ap"))
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    return None


async def get_underlying_price(client: AlpacaMCPClient, symbol: str) -> float:
    """Mid of the latest bid/ask for a stock symbol — used both as the
    option chain's strike-price anchor and as the fill-price estimate for
    stock proposals."""
    result = await client.call_tool("get_stock_latest_quote", {"symbols": symbol})
    quote = _data(result)["quotes"][symbol]
    return (float(quote["bp"]) + float(quote["ap"])) / 2


async def build_option_research(client: AlpacaMCPClient, config: dict, today: date | None = None) -> dict:
    """Option chain snapshots for each underlying in config's whitelist,
    filtered to the configured expiration window and a strike-price band
    around the current underlying price. Returns
    {underlying: {"underlying_price": float, "contracts": {occ_symbol: {...}}}}.

    The strike band is not optional: get_option_chain with no strike bound
    returns whatever the API's default page happens to contain sorted by
    strike ascending, which for a high-priced underlying is deep
    out-of-the-money calls only and zero puts (confirmed live against SPY at
    ~$769 — an unbounded call returned only $420-$675 strike calls with no
    puts at all). Anchoring the band to the real current price via
    get_stock_latest_quote is what makes the fetched chain relevant.

    feed="indicative" is explicit, not incidental: this project's accounts
    have no OPRA subscription (confirmed live — feed="opra" returns 403
    "subscription does not permit querying OPRA data"). The indicative feed
    carries no Greeks/IV — bot/greeks.py derives them from this price data
    instead.
    """
    # US/Eastern, not the host's local date — the CT runs in America/Chicago
    # (an hour behind), which could read "today" as still-yesterday during
    # the CDT/EDT evening gap otherwise.
    today = today or datetime.now(EASTERN).date()
    min_exp = today + timedelta(days=config["min_days_to_expiration"])
    max_exp = today + timedelta(days=config["max_days_to_expiration"])
    band = config["option_strike_band_pct"]

    research = {}
    for underlying in config["underlyings"]:
        price = await get_underlying_price(client, underlying)
        query = {
            "underlying_symbol": underlying,
            "feed": "indicative",
            "expiration_date_gte": min_exp.isoformat(),
            "expiration_date_lte": max_exp.isoformat(),
            "strike_price_gte": round(price * (1 - band), 2),
            "strike_price_lte": round(price * (1 + band), 2),
            "limit": CHAIN_PAGE_LIMIT,
        }
        contracts, pages, truncated = await _fetch_chain_pages(client, query)
        research[underlying] = {
            "underlying_price": price,
            "contracts": contracts,
            "pages": pages,
            "truncated": truncated,
            "max_dte": _max_dte(contracts, today),
        }
    return research


async def _fetch_chain_pages(client: AlpacaMCPClient, query: dict) -> tuple[dict, int, bool]:
    """Follow next_page_token until the chain inside the query's band and
    expiration window is complete. (contracts, pages fetched, truncated).

    The server already applies the expiration window, so the last page IS
    the last in-window expiry - there is nothing to early-stop on. What ends
    the loop: no token (the API sends null on the last page; the test
    fixtures omit the key entirely and mean the same), a token equal to the
    one just sent or an empty page (both would spin forever), or the safety
    cap - the only exit that leaves the menu short, hence `truncated`."""
    contracts: dict = {}
    token = None
    pages = 0
    while True:
        args = dict(query, page_token=token) if token else dict(query)
        data = _data(await client.call_tool("get_option_chain", args))
        pages += 1
        page = data.get("snapshots") or {}
        contracts.update(page)
        next_token = data.get("next_page_token")
        if not next_token or next_token == token or not page:
            return contracts, pages, False
        if pages >= CHAIN_MAX_PAGES:
            return contracts, pages, True
        token = next_token


def _max_dte(contracts: dict, today: date) -> int | None:
    """Furthest expiry actually fetched, in days - the number to compare with
    max_days_to_expiration when asking whether the menu reached the window."""
    dtes = []
    for symbol in contracts:
        try:
            dtes.append((parse_occ_symbol(symbol).expiration - today).days)
        except ValueError:
            continue
    return max(dtes) if dtes else None


def chain_coverage(options: dict) -> dict:
    """Per-underlying fetch coverage for the cycle_start journal event, so the
    day a proposal is refused as unpriceable, the review can see whether the
    menu even reached that expiry."""
    return {
        underlying: {
            "contracts": len(block.get("contracts") or {}),
            "pages": block.get("pages"),
            "max_dte": block.get("max_dte"),
            "truncated": bool(block.get("truncated")),
        }
        for underlying, block in (options or {}).items()
    }


def price_for_proposal(snapshot: dict, p: Proposal) -> float | None:
    """Fill-price estimate for risk sizing, from the snapshot already in
    hand (no extra network call): an option's bid/ask mid (else last
    trade), a stock's underlying price. None if the snapshot has nothing
    for that symbol - risk.py rejects a None/zero price, so an unknown
    symbol can't sneak through unsized."""
    options = snapshot.get("options") or {}
    if p.instrument == "option":
        research = options.get(p.underlying or "") or {}
        raw = (research.get("contracts") or {}).get(p.symbol)
        if not raw:
            return None
        quote = raw.get("latestQuote") or {}
        bid, ask = quote.get("bp"), quote.get("ap")
        if bid and ask and bid > 0 and ask > 0:
            return (bid + ask) / 2
        last = (raw.get("latestTrade") or {}).get("p")
        return float(last) if last else None
    research = options.get(p.symbol) or {}
    return research.get("underlying_price")


async def build_open_orders(client: AlpacaMCPClient) -> list[dict] | None:
    """Orders resting at the broker, normalised (#171). None when the lookup
    fails - the cycle must not die for it, but "unknown" and "none" are
    different facts and the prompt says which."""
    try:
        data = _data(await client.call_tool("get_orders", {"status": "open", "nested": True}))
    except Exception:  # noqa: BLE001 - never worth a cycle
        return None
    raw = data.get("result", data) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return None
    out = []
    for o in raw:
        if not isinstance(o, dict) or not o.get("symbol") or not o.get("id"):
            continue
        out.append({
            "id": str(o["id"]),
            "client_order_id": o.get("client_order_id"),
            "symbol": str(o["symbol"]),
            "side": str(o.get("side") or "").lower(),
            "qty": _optional_float(o.get("qty")) or 0.0,
            "filled_qty": _optional_float(o.get("filled_qty")) or 0.0,
            "order_type": str(o.get("type") or o.get("order_type") or "market"),
            "limit_price": _optional_float(o.get("limit_price")),
            "submitted_at": o.get("submitted_at") or o.get("created_at"),
            "instrument": "option" if o.get("asset_class") == "us_option" else "stock",
        })
    return out


def _serialize_account(account: AccountState, open_orders: list[dict] | None = None) -> dict:
    return {
        "equity": account.equity,
        "start_of_day_equity": account.start_of_day_equity,
        "cash": account.cash,
        "account_number": account.account_number,
        "open_orders": open_orders,
        "positions": [
            {
                "symbol": p.symbol,
                "instrument": p.instrument,
                "qty": p.qty,
                "market_value": p.market_value,
                "underlying": p.underlying,
                "avg_entry_price": p.avg_entry_price,
                "current_price": p.current_price,
            }
            for p in account.positions.values()
        ],
    }


async def build_snapshot(client: AlpacaMCPClient, config: dict, now: datetime | None = None) -> dict:
    """Everything the decision loop needs for one cycle, in one
    JSON-serializable dict — this is what eventually gets embedded in the
    LLM prompt (a later step), mirroring alpaca-trader's build_snapshot()
    shape even though the exact consumer doesn't exist yet."""
    now = now or datetime.now(EASTERN)
    clock_data = _data(await client.call_tool("get_clock"))
    account = await build_account_state(client)
    open_orders = await build_open_orders(client)
    options = await build_option_research(client, config, today=now.date())

    predictions = {}
    if config.get("predictions_enabled"):
        # Kalshi prior (#44): read-only, cached, never fatal.
        from bot.predictions import DEFAULT_SERIES, chain_summary, fetch_predictions

        predictions = await fetch_predictions(config)
        # The chain's own odds (#140), from the ladder just fetched above -
        # computed even when Kalshi came back empty, which is the point:
        # SPY options have no thin-volume failure mode, so the model keeps
        # one crowd estimate on the days the event market is withheld.
        for sym in config.get("prediction_series") or DEFAULT_SERIES:
            contracts = (options.get(sym) or {}).get("contracts") or {}
            if not contracts:
                continue
            # The ETF's OWN previous close, never Kalshi's: that reference is
            # the S&P 500 INDEX level (~7712), and these strikes are SPY the
            # ETF (~766). Percent moves line up across the two; levels never.
            reference = None
            try:
                result = _data(await client.call_tool("get_stock_snapshot", {"symbols": sym, "feed": "iex"}))
                bar = ((result.get("snapshots") or result).get(sym) or {}).get("prevDailyBar") or {}
                reference = float(bar["c"]) if bar.get("c") else None
            except Exception:  # noqa: BLE001 - a prior is never worth a cycle
                reference = None
            chain = chain_summary(contracts, reference)
            if chain:
                predictions.setdefault(sym, {})["chain"] = chain

    return {
        "market_open": clock_data.get("is_open", False),
        "next_open": clock_data.get("next_open"),
        "next_close": clock_data.get("next_close"),
        "account": _serialize_account(account, open_orders),
        "options": options,
        "predictions": predictions,
    }

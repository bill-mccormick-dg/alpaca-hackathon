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


def _data(result) -> dict:
    """Unwrap an MCP tool_call result's {"data": ...} envelope."""
    text = "\n".join(block.text for block in getattr(result, "content", []) if hasattr(block, "text"))
    payload = json.loads(text)
    return payload.get("data", payload) if isinstance(payload, dict) else payload


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
    )


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
        result = await client.call_tool(
            "get_option_chain",
            {
                "underlying_symbol": underlying,
                "feed": "indicative",
                "expiration_date_gte": min_exp.isoformat(),
                "expiration_date_lte": max_exp.isoformat(),
                "strike_price_gte": round(price * (1 - band), 2),
                "strike_price_lte": round(price * (1 + band), 2),
                "limit": 500,
            },
        )
        data = _data(result)
        research[underlying] = {
            "underlying_price": price,
            "contracts": data.get("snapshots", {}),
        }
    return research


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


def _serialize_account(account: AccountState) -> dict:
    return {
        "equity": account.equity,
        "start_of_day_equity": account.start_of_day_equity,
        "cash": account.cash,
        "positions": [
            {
                "symbol": p.symbol,
                "instrument": p.instrument,
                "qty": p.qty,
                "market_value": p.market_value,
                "underlying": p.underlying,
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
    options = await build_option_research(client, config, today=now.date())

    return {
        "market_open": clock_data.get("is_open", False),
        "next_open": clock_data.get("next_open"),
        "next_close": clock_data.get("next_close"),
        "account": _serialize_account(account),
        "options": options,
    }

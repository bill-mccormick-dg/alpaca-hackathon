"""Read-only research tools the model may call before deciding (issue #43).

The model sees a curated, simplified tool set - never Alpaca's raw 72
tools and never anything that places, cancels, or closes. Each tool maps
to one MCP read tool with fixed safe arguments (indicative options feed,
bounded lookbacks and limits), and every call is journaled so the
reasoning trail is auditable. Order placement stays exactly where it
was: the model returns proposals, risk.py judges them.
"""

import json

from bot.alpaca_mcp import AlpacaMCPClient

MAX_RESULT_CHARS = 3500

# OpenAI-style tool schemas. Deliberately few parameters: the model picks
# a symbol and a scale, we choose feeds/limits.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_bars",
            "description": "Recent OHLCV price bars for a STOCK/ETF ticker (the underlying, not an option). "
                           "Use for intraday trend and where price sits versus the day's range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker, e.g. SPY"},
                    "timeframe": {"type": "string", "enum": ["5Min", "15Min", "1Hour", "1Day"], "description": "Bar size"},
                    "lookback_hours": {"type": "integer", "minimum": 1, "maximum": 120, "description": "How far back (hours)"},
                },
                "required": ["symbol", "timeframe"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_snapshot",
            "description": "Latest trade, quote, current minute bar, today's bar and previous day's bar for a stock/ETF.",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_option_snapshot",
            "description": "Latest quote/trade and bars for specific option CONTRACTS (OCC symbols, up to 10, comma-separated). "
                           "Use to look closer at a contract from the snapshot or a nearby strike/expiry.",
            "parameters": {
                "type": "object",
                "properties": {"symbols": {"type": "string", "description": "e.g. SPY260904C00770000,SPY260904P00770000"}},
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "Recent news headlines for one or more tickers (comma-separated). Use for catalysts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["symbols"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def _symbols(value, max_n: int) -> str:
    parts = [p.strip().upper() for p in str(value or "").split(",") if p.strip()]
    return ",".join(parts[:max_n])


def to_mcp_call(name: str, args: dict) -> tuple[str, dict]:
    """Translate a model tool call into an MCP tool + safe arguments.
    Raises ValueError for anything outside the allowlist."""
    args = args or {}
    if name == "get_bars":
        tf = str(args.get("timeframe") or "15Min")
        if tf not in ("5Min", "15Min", "1Hour", "1Day"):
            tf = "15Min"
        hours = int(args.get("lookback_hours") or {"5Min": 8, "15Min": 24, "1Hour": 72, "1Day": 120 * 24}[tf])
        hours = max(1, min(hours, 120 if tf != "1Day" else 120 * 24))
        return "get_stock_bars", {
            "symbols": _symbols(args.get("symbol"), 1),
            "timeframe": tf,
            "hours": hours,
            "days": 0,
            "limit": 120,
            "feed": "iex",
            "sort": "asc",
        }
    if name == "get_stock_snapshot":
        return "get_stock_snapshot", {"symbols": _symbols(args.get("symbol"), 1), "feed": "iex"}
    if name == "get_option_snapshot":
        return "get_option_snapshot", {"symbols": _symbols(args.get("symbols"), 10), "feed": "indicative"}
    if name == "get_news":
        limit = int(args.get("limit") or 5)
        return "get_news", {"symbols": _symbols(args.get("symbols"), 5), "limit": max(1, min(limit, 10)),
                            "sort": "desc", "include_content": False}
    raise ValueError(f"tool {name!r} is not available")


def _trim_bars(data):
    """Bars come back verbose; keep t/o/h/l/c/v so 120 bars fit the budget."""
    if isinstance(data, dict) and isinstance(data.get("bars"), dict):
        return {sym: [{k: b.get(k) for k in ("t", "o", "h", "l", "c", "v")} for b in bars if isinstance(b, dict)]
                for sym, bars in data["bars"].items()}
    return data


def _trim_news(data):
    if isinstance(data, dict) and isinstance(data.get("news"), list):
        return [{k: n.get(k) for k in ("created_at", "headline", "summary", "symbols", "source")} for n in data["news"]
                if isinstance(n, dict)]
    return data


def result_text(result, name: str) -> str:
    """MCP result -> compact JSON string for the tool message, trimmed."""
    text = "\n".join(b.text for b in getattr(result, "content", []) if hasattr(b, "text"))
    try:
        payload = json.loads(text)
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
    except json.JSONDecodeError:
        return text[:MAX_RESULT_CHARS]
    if name == "get_bars":
        data = _trim_bars(data)
    elif name == "get_news":
        data = _trim_news(data)
    out = json.dumps(data, separators=(",", ":"), default=str)
    return out if len(out) <= MAX_RESULT_CHARS else out[:MAX_RESULT_CHARS] + '..."(truncated)'


async def execute_tool_call(mcp: AlpacaMCPClient, name: str, args: dict) -> str:
    """Run one allowlisted tool. Errors become a string the model can read
    and move on from - a bad tool call must never end the cycle."""
    try:
        mcp_name, mcp_args = to_mcp_call(name, args)
    except ValueError as exc:
        return json.dumps({"error": str(exc), "available": sorted(TOOL_NAMES)})
    try:
        result = await mcp.call_tool(mcp_name, mcp_args)
    except Exception as exc:  # noqa: BLE001 - tool failures are data for the model, not crashes
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]})
    return result_text(result, name)

"""Decision step: hands a snapshot to Featherless, gets back risk-unchecked
Proposals. Mirrors alpaca-trader's trader/decide.py, adapted for options and
for Featherless's OpenAI-compatible chat API instead of the Claude CLI.

Nothing here places an order or talks to Alpaca — this module's only
network call is to Featherless. bot/risk.py has the final say on every
proposal this produces.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from bot import greeks, journal, predictions, research
from bot.featherless import FeatherlessClient
from bot.models import Proposal
from bot.occ import parse_occ_symbol
from bot.risk import EASTERN

PROMPT_TEMPLATE = """You are the decision engine of an autonomous PAPER-trading agent on \
Alpaca, built for a hackathon options-trading challenge. You run periodically during US \
market hours and decide what, if anything, to do this cycle. Doing nothing is a perfectly \
good decision - only propose a trade when you see a concrete reason in the data below.

Long-only throughout: you may BUY to open or add to a position, or SELL to close/reduce one \
you already hold. Never propose selling a symbol you do not hold - that would be a naked \
short, which this account cannot do. Whole-number quantities only (shares or option \
contracts). Market or limit orders only.

Options trading is the core of this challenge - your strategy must use it, not just stock. \
Stock trades are allowed but should support your options thesis rather than replace it.

Hard limits (enforced downstream in code; a violating proposal is rejected outright and \
never adjusted for you, so propose within them): max ${max_position_usd:.0f} total notional \
per position (existing + new, combined), max {max_positions} concurrent positions (a resting buy \
counts as one), max \
{max_contracts_per_order} option contracts per single order, whitelist symbols only \
({underlyings}), options must have between {min_dte} and {max_dte} days to expiration. New \
entries (buys) are rejected after {last_entry} ET; sells remain legal until {trade_end} ET.

How long a position can actually live, which is not the same as the expiration window above: \
code closes any option once it has {expiry_close_dte} day(s) to expiration, and an end-of-day \
backstop closes anything with {eod_close_dte} day(s) or fewer left. So a contract you buy at \
{eod_close_dte} DTE or nearer will be sold the same afternoon regardless of how it is doing - \
choose an expiration that gives your thesis room to play out, or do not open the position.

Below is the account state and, per whitelisted underlying, its current price and the \
{contracts_per_underlying} option contracts nearest the money within the tradeable \
expiration window. Each contract lists strike, days to expiration, and bid/ask/last, plus \
spread_pct where both sides of the quote exist: the bid/ask spread as a percentage of the mid. \
spread_pct is what a buy-then-sell round trip at market costs you before the underlying moves \
at all (entry and exit each pay half the spread relative to mid) - your thesis must clear at \
least that much just to break even. Prefer the tighter contract when two express the same view. \
Each \
contract also carries implied volatility and the standard Greeks (delta, gamma, theta per day, \
vega per 1 vol point, rho) as Alpaca computes them (greeks_source "alpaca"). Where Alpaca has \
none - a thin or stale quote - they were derived from the contract's own price via \
Black-Scholes instead (greeks_source "derived"): treat those as rough guides only. A contract \
with no Greeks at all had price data too thin to solve - judge that one on price, strike, and \
days-to-expiration alone. Either way, do NOT spend effort auditing or reconciling the data - \
skip anything that looks broken and decide from what is plausible. Keep your reasoning brief \
and answer decisively.

POSITIONS YOU HOLD, when present below, is the authoritative list of what you own. A sell may \
name only a symbol from that list, copied exactly: the option menu lists neighbouring strikes at \
the same expiry right beside what you hold, and a sell that names one of those is not a sale of \
your position - it is refused (or, when only the strike differs, rewritten to the held contract). \
Each position carries the reason you gave when you opened it and the prediction-market prior at \
that moment, next to the prior now. Judge whether a thesis has changed by comparing those two \
priors and today's price action against the stated reason - not by re-deriving a view from \
scratch every cycle. RESTING ORDERS are buys you already sent that have not filled: they count \
against your caps, and sending the same idea again doubles the position when both fill.

STRATEGY (from config.yaml - the thesis you are executing; the hard limits above still win):
{strategy_notes}
{learning}{predictions}{positions}{tools_note}SNAPSHOT:
{snapshot}

Respond with ONLY a JSON array (no markdown fence, no prose) of zero or more actions:
[{{"instrument": "option"|"stock", "symbol": "<OCC symbol or ticker>", "side": "buy"|"sell", \
"qty": <whole number>, "order_type": "market"|"limit", "limit_price": <optional, limit \
orders only>, "reason": "<one sentence>"}}]
An empty array [] means hold everything and do nothing this cycle.
"""


def _contract_market_price(raw: dict) -> float | None:
    """Best available price for a contract: mid of bid/ask, else last
    trade, else the prior daily close. None if nothing usable at all."""
    quote = raw.get("latestQuote") or {}
    bid, ask = quote.get("bp"), quote.get("ap")
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2
    trade = raw.get("latestTrade") or {}
    if trade.get("p"):
        return float(trade["p"])
    daily = raw.get("dailyBar") or {}
    if daily.get("c"):
        return float(daily["c"])
    return None


def _summarize_contract(symbol: str, raw: dict, spot: float, today: date) -> dict | None:
    try:
        occ = parse_occ_symbol(symbol)
    except ValueError:
        return None  # not an OCC symbol; shouldn't happen from Alpaca's own chain
    dte = (occ.expiration - today).days
    if dte <= 0:
        return None  # already expired as of "today" - not tradeable this cycle anyway

    quote = raw.get("latestQuote") or {}
    entry = {
        "symbol": symbol,
        "type": occ.option_type,
        "strike": occ.strike,
        "dte": dte,
        "bid": quote.get("bp"),
        "ask": quote.get("ap"),
        "last": (raw.get("latestTrade") or {}).get("p"),
    }

    bid, ask = quote.get("bp"), quote.get("ap")
    if bid and ask and bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        entry["spread_pct"] = round((ask - bid) / mid * 100, 1)

    # Alpaca's snapshot carries IV and Greeks on most contracts (#160 - the
    # "free feed has none" belief was wrong; the far-OTM and quote-less ones
    # lack them). Prefer those: computed on one surface with rates and
    # dividends, so put and call deltas at a strike agree. Black-Scholes on
    # our side is the fallback for the rest, and is marked as such.
    provided = _alpaca_greeks(raw)
    if provided:
        entry.update(provided, greeks_source="alpaca")
        return entry

    price = _contract_market_price(raw)
    if price is not None:
        g = greeks.greeks(price, spot, occ.strike, dte / 365, occ.option_type)
        if g is not None:
            entry.update(
                iv=round(g.implied_vol, 4),
                delta=round(g.delta, 4),
                gamma=round(g.gamma, 5),
                theta=round(g.theta, 4),
                vega=round(g.vega, 4),
                greeks_source="derived",
            )
    return entry


def _alpaca_greeks(raw: dict) -> dict | None:
    """The snapshot's own impliedVolatility + greeks block, in the prompt's
    field names and rounding; None unless IV and delta are both present."""
    g = raw.get("greeks") or {}
    iv = raw.get("impliedVolatility")
    if not isinstance(g, dict) or iv is None or g.get("delta") is None:
        return None
    try:
        out = {"iv": round(float(iv), 4), "delta": round(float(g["delta"]), 4)}
        for name, places in (("gamma", 5), ("theta", 4), ("vega", 4), ("rho", 4)):
            if g.get(name) is not None:
                out[name] = round(float(g[name]), places)
    except (TypeError, ValueError):
        return None
    return out


def _summarize_options(snapshot: dict, config: dict, today: date) -> dict:
    """Trims each underlying's option chain to the N contracts nearest the
    money (config's research_contracts_per_underlying), regardless of how
    many bot/snapshot.py fetched - keeps the prompt bounded and relevant
    without another network round-trip.

    Chosen from the OCC symbol alone, then summarized: the summary solves
    Black-Scholes per contract, and since #158 the fetched chain can be
    thousands of contracts, so the solve runs on the N that make the prompt,
    not the pool. The filters here (unparseable symbol, already expired)
    mirror _summarize_contract's so the slice is never left short."""
    limit = int(config.get("research_contracts_per_underlying", 12))
    summarized = {}
    for underlying, per_name in (snapshot.get("options") or {}).items():
        spot = per_name.get("underlying_price")
        if not spot:
            summarized[underlying] = {"underlying_price": None, "contracts": []}
            continue

        candidates = []
        for symbol, raw in (per_name.get("contracts") or {}).items():
            try:
                occ = parse_occ_symbol(symbol)
            except ValueError:
                continue
            dte = (occ.expiration - today).days
            if dte <= 0:
                continue
            candidates.append((abs(occ.strike - spot), dte, symbol, raw))
        candidates.sort(key=lambda c: (c[0], c[1]))

        contracts = []
        for _, _, symbol, raw in candidates[:limit]:
            entry = _summarize_contract(symbol, raw, spot, today)
            if entry is not None:
                contracts.append(entry)

        summarized[underlying] = {"underlying_price": spot, "contracts": contracts}
    return summarized


TOOLS_NOTE = """RESEARCH TOOLS: you may call the provided read-only tools (recent bars, a stock snapshot, \
specific option contracts, news) up to {n} times before answering, to check a trend or a catalyst \
or to look at a strike/expiry not in the snapshot. Use them only when they would change your \
decision; then answer with the JSON array. Tools never place orders.

"""


def build_prompt(
    snapshot: dict, config: dict, today: date | None = None, tools: bool = False, learning: str = "",
    positions_block: str = "",
) -> str:
    today = today or datetime.now(EASTERN).date()
    payload = {
        "account": snapshot.get("account"),
        "options": _summarize_options(snapshot, config, today),
    }
    tools_note = TOOLS_NOTE.format(n=int(config.get("research_max_tool_calls", 6))) if tools else ""
    return PROMPT_TEMPLATE.format(
        tools_note=tools_note,
        learning=learning or "",
        predictions=predictions.prompt_block(snapshot.get("predictions") or {}),
        # bot/holdings.py - the held symbols with their entry thesis and the
        # prior then vs now (#170, #173). Sits after the prior it refers to.
        positions=positions_block or "",
        max_position_usd=float(config["max_position_usd"]),
        max_positions=int(config["max_positions"]),
        max_contracts_per_order=int(config["max_contracts_per_order"]),
        underlyings=", ".join(config["underlyings"]),
        min_dte=int(config["min_days_to_expiration"]),
        max_dte=int(config["max_days_to_expiration"]),
        last_entry=str(config["last_entry"]),
        trade_end=str(config["trade_end"]),
        # The model used to be told the expiration window but not the rules that
        # END a position, so it could propose a short-dated contract in good
        # faith that the 15:50 backstop would close hours later - which reads as
        # model error in the journal when it is actually policy it never saw.
        expiry_close_dte=int(config.get("expiry_close_dte", 0)),
        eod_close_dte=int(config.get("eod_close_dte", 1)),
        contracts_per_underlying=int(config.get("research_contracts_per_underlying", 12)),
        strategy_notes=str(config.get("strategy_notes") or "(none)").strip(),
        snapshot=json.dumps(payload, separators=(",", ":")),
    )


def _extract_json_array(text: str) -> list:
    text = text.strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON array in model output: {text[:200]!r}")
    return json.loads(m.group(0))


def _parse_proposal(action: dict) -> Proposal:
    """Builds a Proposal from a raw model action dict without validating
    it - deliberately, per bot/models.py's philosophy: a model can propose
    garbage, and bot/risk.py's check_order() is the one place that rejects
    it with a clear reason. Malformed/missing fields become deliberately
    invalid Proposal values (qty=0, side="") rather than raising, so one
    bad action in the array doesn't blow up the whole decide() call."""
    instrument = str(action.get("instrument", "")).lower()
    symbol = str(action.get("symbol", "")).upper()

    underlying = None
    if instrument == "option":
        try:
            underlying = parse_occ_symbol(symbol).underlying
        except ValueError:
            underlying = None  # left None; risk.py's malformed-symbol check rejects this

    try:
        qty = int(float(action.get("qty", 0)))
    except (TypeError, ValueError):
        qty = 0

    limit_price = action.get("limit_price")
    try:
        limit_price = float(limit_price) if limit_price is not None else None
    except (TypeError, ValueError):
        limit_price = None

    return Proposal(
        instrument=instrument,
        symbol=symbol,
        side=str(action.get("side", "")).lower(),
        qty=qty,
        order_type=str(action.get("order_type") or "market").lower(),
        limit_price=limit_price,
        underlying=underlying,
        reason=str(action.get("reason", "")),
    )


@dataclass
class Decision:
    proposals: list[Proposal]
    raw: str
    model: str
    usage: dict | None = None  # Featherless/OpenAI-style {prompt_tokens, completion_tokens, total_tokens}
    latency_sec: float = 0.0
    finish_reason: str | None = None
    reasoning: str = ""  # thinking models return this separately from content; kept for the audit trail
    tool_calls: list = field(default_factory=list)  # research calls made before answering (#43)
    extra: dict = field(default_factory=dict)


REASONING_KEEP_CHARS = 2000


class TruncatedOutput(ValueError):
    """The model hit max_tokens before producing an answer - with thinking
    models that usually means the hidden reasoning consumed the budget."""


def _sampling_kwargs(config: dict) -> dict:
    """temperature / max_tokens from config, only when present, so a config
    without them behaves exactly as before. `model_params` (a dict) is
    merged verbatim into the request body - the escape hatch for
    model-specific controls such as reasoning/thinking toggles
    (chat_template_kwargs, reasoning_effort, thinking), which differ per
    model family and belong in the variant's config, not in code."""
    kwargs = {}
    if config.get("temperature") is not None:
        kwargs["temperature"] = float(config["temperature"])
    if config.get("max_tokens") is not None:
        kwargs["max_tokens"] = int(config["max_tokens"])
    extra = config.get("model_params")
    if isinstance(extra, dict):
        kwargs.update(extra)
    return kwargs


def _research_enabled(config: dict, mcp) -> bool:
    return bool(config.get("research_tools_enabled")) and mcp is not None


async def _chat_with_research(client: FeatherlessClient, mcp, config: dict, prompt: str) -> tuple[dict, list[dict], dict]:
    """The bounded research loop (issue #43): the model may call read-only
    tools up to research_max_tool_calls times, then must answer. Returns
    (final response, tool_calls made, summed usage). Every call is
    journaled as a `tool_call` event - the audit trail of what the agent
    looked at before deciding."""
    messages = [{"role": "user", "content": prompt}]
    kwargs = _sampling_kwargs(config)
    budget = int(config.get("research_max_tool_calls", 6))
    made: list[dict] = []
    total_usage: dict = {}

    def add_usage(u):
        for k, v in (u or {}).items():
            if isinstance(v, (int, float)):
                total_usage[k] = total_usage.get(k, 0) + v

    while True:
        tools_left = budget - len(made)
        response = await client.chat(
            messages, tools=research.TOOLS if tools_left > 0 else None, **kwargs
        ) if tools_left > 0 else await client.chat(messages, **kwargs)
        add_usage(response.get("usage"))
        message = (response.get("choices") or [{}])[0].get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls or tools_left <= 0:
            if total_usage:
                response["usage"] = total_usage
            return response, made, total_usage

        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls[:tools_left]:
            fn = (call.get("function") or {})
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            started = time.monotonic()
            result = await research.execute_tool_call(mcp, name, args)
            elapsed = round(time.monotonic() - started, 3)
            made.append({"name": name, "args": args, "chars": len(result), "sec": elapsed})
            journal.log("tool_call", tool=name, args=args, result_chars=len(result), latency_sec=elapsed,
                        result_head=result[:300])
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name, "content": result})
        if len(made) >= budget:
            messages.append({"role": "user", "content": "Research budget used. Answer now with ONLY the JSON array."})


async def decide(
    snapshot: dict, config: dict, client: FeatherlessClient, today: date | None = None, mcp=None,
    learning: str = "", positions_block: str = "",
) -> Decision:
    """One decision. With research tools enabled (config + an MCP client),
    the model may look things up first (bounded); otherwise a single call.
    `learning` is the optional RECENT OUTCOMES block (bot/learning.py).
    No retry - that's an operational concern for run_cycle.py. The Decision
    carries usage, latency and the tool calls made so the journal can
    attribute cost, speed and evidence to the model/config that produced it."""
    use_tools = _research_enabled(config, mcp)
    prompt = build_prompt(snapshot, config, today, tools=use_tools, learning=learning, positions_block=positions_block)
    started = time.monotonic()
    tool_calls: list[dict] = []
    if use_tools:
        response, tool_calls, _ = await _chat_with_research(client, mcp, config, prompt)
    else:
        response = await client.chat([{"role": "user", "content": prompt}], **_sampling_kwargs(config))
    latency = time.monotonic() - started
    # A 200 carrying an error object instead of choices (rate limit, spent
    # credits, model unavailable) used to surface as a bare
    # KeyError: 'choices' in the journal, which says nothing about the
    # actual cause - seen live on both accounts. Keep the provider's own
    # message: on a metered credit during a scored week, "why did the model
    # not answer" needs to be answerable from the journal alone.
    if not response.get("choices"):
        detail = response.get("error") or response
        raise RuntimeError(f"model returned no choices: {json.dumps(detail, default=str)[:300]}")
    choice = response["choices"][0]
    message = choice.get("message") or {}
    raw = message.get("content") or ""
    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
    finish = choice.get("finish_reason")
    usage = response.get("usage")

    if not raw.strip() and finish == "length":
        used = (usage or {}).get("completion_tokens")
        raise TruncatedOutput(
            f"model output truncated before an answer (finish_reason=length, {used} completion tokens, "
            f"{len(reasoning)} chars of hidden reasoning) - raise max_tokens or disable thinking via model_params"
        )
    actions = _extract_json_array(raw)
    proposals = [_parse_proposal(a) for a in actions if isinstance(a, dict)]
    return Decision(
        proposals=proposals,
        raw=raw,
        model=response.get("model") or client.model,
        usage=usage,
        latency_sec=round(latency, 3),
        finish_reason=finish,
        reasoning=reasoning[:REASONING_KEEP_CHARS],
        tool_calls=tool_calls,
    )

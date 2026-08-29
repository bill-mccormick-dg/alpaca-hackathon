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

from bot import greeks
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
per position (existing + new, combined), max {max_positions} concurrent positions, max \
{max_contracts_per_order} option contracts per single order, whitelist symbols only \
({underlyings}), options must have between {min_dte} and {max_dte} days to expiration. New \
entries (buys) are rejected after {last_entry} ET; sells remain legal until {trade_end} ET.

Below is the account state and, per whitelisted underlying, its current price and the \
{contracts_per_underlying} option contracts nearest the money within the tradeable \
expiration window. Each contract lists strike, days to expiration, and bid/ask/last. Alpaca's \
feed carries no Greeks or implied volatility, so where a contract's price was stable enough \
to solve for it, implied volatility and the standard Greeks (delta, gamma, theta per day, \
vega per 1 vol point) were derived from it via Black-Scholes and are included too. A contract \
missing those fields means the price data was too thin or stale to solve reliably - judge \
that one on price, strike, and days-to-expiration alone. The Greeks are solved independently \
per contract from a free indicative feed, so they will not be internally consistent (put and \
call deltas at one strike need not sum to -1, and a few quotes are plainly stale). Treat them as \
rough guides; do NOT spend effort auditing or reconciling the data - skip anything that looks \
broken and decide from what is plausible. Keep your reasoning brief and answer decisively.

STRATEGY (from config.yaml - the thesis you are executing; the hard limits above still win):
{strategy_notes}
SNAPSHOT:
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
            )
    return entry


def _summarize_options(snapshot: dict, config: dict, today: date) -> dict:
    """Trims each underlying's option chain to the N contracts nearest the
    money (config's research_contracts_per_underlying), regardless of how
    many bot/snapshot.py fetched - keeps the prompt bounded and relevant
    without another network round-trip."""
    limit = int(config.get("research_contracts_per_underlying", 12))
    summarized = {}
    for underlying, research in (snapshot.get("options") or {}).items():
        spot = research.get("underlying_price")
        if not spot:
            summarized[underlying] = {"underlying_price": None, "contracts": []}
            continue

        contracts = []
        for symbol, raw in (research.get("contracts") or {}).items():
            entry = _summarize_contract(symbol, raw, spot, today)
            if entry is not None:
                contracts.append(entry)
        contracts.sort(key=lambda c: (abs(c["strike"] - spot), c["dte"]))

        summarized[underlying] = {"underlying_price": spot, "contracts": contracts[:limit]}
    return summarized


def build_prompt(snapshot: dict, config: dict, today: date | None = None) -> str:
    today = today or datetime.now(EASTERN).date()
    payload = {
        "account": snapshot.get("account"),
        "options": _summarize_options(snapshot, config, today),
    }
    return PROMPT_TEMPLATE.format(
        max_position_usd=float(config["max_position_usd"]),
        max_positions=int(config["max_positions"]),
        max_contracts_per_order=int(config["max_contracts_per_order"]),
        underlyings=", ".join(config["underlyings"]),
        min_dte=int(config["min_days_to_expiration"]),
        max_dte=int(config["max_days_to_expiration"]),
        last_entry=str(config["last_entry"]),
        trade_end=str(config["trade_end"]),
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


async def decide(
    snapshot: dict, config: dict, client: FeatherlessClient, today: date | None = None
) -> Decision:
    """One Featherless call, no retry - that's an operational concern for
    whatever calls this (run_cycle.py), not this module. The Decision
    carries usage and latency so the journal can attribute cost and speed
    to the model/config that produced it."""
    prompt = build_prompt(snapshot, config, today)
    started = time.monotonic()
    response = await client.chat([{"role": "user", "content": prompt}], **_sampling_kwargs(config))
    latency = time.monotonic() - started
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
    )

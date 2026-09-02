#!/usr/bin/env python3
"""Check the models we run against Featherless's live catalog — not part of
the suite. The evidence behind docs/strategy.md "Why these models".

Every mechanical claim in that section is a number this script prints from the
API rather than a snapshot someone typed once: tool calling, context length,
plan availability, price, and whether the model is loaded right now. A doc that
asserts "32k context, tool_use, $0.60/M" rots the moment Featherless changes a
plan or retires a model, and nothing tells you. This does.

    python scripts/verify_models.py                      # official config
    python scripts/verify_models.py --config config-test.yaml
    python scripts/verify_models.py --all-configs        # every config we ship
    python scripts/verify_models.py --model mukaj/Llama-3.1-Hawkish-8B
    python scripts/verify_models.py --all-configs --rejected   # the whole documented claim

Exits non-zero when a model a config actually points at fails a gate. The
documented rejects are expected to fail and do not affect the exit code.

What it does NOT measure is the criterion that actually decided things:
whether the model follows the instructions in the prompt (position count,
DTE band, evidence quality). That needs the prompt and real market data —
farm.py on a challenger account, never the judged one. The gates below are
necessary, not sufficient; see the rejected-alternatives table in strategy.md.
"""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import load_config
from bot.credentials import load_credentials
from bot.featherless import BASE_URL

# The configs we ship, in the order the docs discuss them. Each contributes its
# `model` and its resolved reviewer, so a config that quietly points at a model
# nobody has checked shows up here.
CONFIGS = ("config.yaml", "config-test.yaml", "config-variants/mixed.yaml")

# Documented rejected alternatives (strategy.md). Kept here so the claims about
# them stay checkable too - "not in the catalog at all" is a claim that can
# stop being true.
REJECTED = (
    "mukaj/Llama-3.1-Hawkish-8B",
    "KBTG-Labs/THaLLE-0.2-ThaiLLM-8B-fa",
    "AdaptLLM/finance-chat",
    "hyokwan/familidata",
)

# One decision prompt is ~5k tokens with 60 contracts (5,246 measured
# 2026-08-30), and the answer needs room after it. A 4k model cannot be handed
# the prompt at all, which is a hard disqualification rather than a judgement.
DECIDE_CONTEXT_MIN = 8192
# The bounded research loop accumulates prompt tokens across iterations - one
# test-account decision measured 95,328 (2026-08-31, Qwen3.8-Flash-Next, six
# tool calls). That is far past that model's ADVERTISED 32k window and
# Featherless answered anyway (finish_reason=stop), so this is reported as a
# note, not enforced as a gate: the catalog's context_length is a lower bound
# on what the endpoint accepts, and we do not know the real ceiling.
RESEARCH_PEAK_TOKENS = 95_328


def fetch_catalog(key: str) -> list[dict]:
    r = httpx.get(f"{BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=60)
    r.raise_for_status()
    body = r.json()
    return body["data"] if isinstance(body, dict) else body


def fetch_one(key: str, model: str) -> dict | None:
    """The per-model endpoint carries `availability`, which the list does not -
    a COLD model is one that has to load before it answers, and a 10-minute
    cron with a 60-second timeout forfeits the cycle while it does."""
    r = httpx.get(
        f"{BASE_URL}/models/{model}", headers={"Authorization": f"Bearer {key}"}, timeout=60
    )
    return r.json() if r.status_code == 200 else None


def _row(model: str, entry: dict | None, detail: dict | None) -> str:
    if entry is None:
        return f"{model:<42} NOT IN CATALOG"
    ctx = entry.get("context_length") or 0
    tools = bool((entry.get("features") or {}).get("tool_use"))
    plan = bool(entry.get("available_on_current_plan"))
    price = entry.get("pricing") or {}
    tier = ((detail or {}).get("availability") or {}).get("tier", "?")
    return (
        f"{model:<42} {'yes' if tools else 'NO ':<5} {ctx:>7,} "
        f"{'yes' if plan else 'NO ':<5} "
        f"${price.get('input', '?')}/${price.get('output', '?')}".ljust(78)
        + f" {tier}"
    )


def _verdict(model: str, entry: dict | None, uses_research: bool) -> tuple[list[str], list[str]]:
    """The mechanical gates, each tied to something in the code. Returns
    (failures, notes) - a failure means we could not run this model at all."""
    if entry is None:
        return (["not in the API catalog - the UI may list it; we cannot call it"], [])
    failures, notes = [], []
    if not (entry.get("features") or {}).get("tool_use"):
        # A hard gate whether or not THIS config enables research: any model we
        # would adopt has to be able to take either seat, and the challenger
        # config is where a new model gets tried first.
        failures.append("no tool_use - bot/research.py's four tools cannot be offered")
    if not entry.get("available_on_current_plan"):
        failures.append("not on our plan")
    ctx = entry.get("context_length") or 0
    if ctx < DECIDE_CONTEXT_MIN:
        failures.append(f"context {ctx:,} < {DECIDE_CONTEXT_MIN:,} - the decision prompt does not fit")
    elif uses_research and ctx < RESEARCH_PEAK_TOKENS:
        notes.append(
            f"advertised context {ctx:,} is under the {RESEARCH_PEAK_TOKENS:,} prompt tokens "
            "a research cycle has actually used - served anyway, but unbudgeted"
        )
    return failures, notes


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default="config.yaml", help="config whose models to check")
    ap.add_argument("--all-configs", action="store_true", help=f"check all of: {', '.join(CONFIGS)}")
    ap.add_argument("--model", action="append", default=[], help="also check this model id (repeatable)")
    ap.add_argument("--rejected", action="store_true", help="also check the documented rejected alternatives")
    args = ap.parse_args()

    # Same key on every account; "test" is the one that resolves locally.
    key = load_credentials("test")["FEATHERLESS_API_KEY"]
    catalog = fetch_catalog(key)
    by_id = {m["id"]: m for m in catalog}

    tool_models = [m for m in catalog if (m.get("features") or {}).get("tool_use")]
    on_plan = [m for m in tool_models if m.get("available_on_current_plan")]
    roomy = [m for m in on_plan if (m.get("context_length") or 0) >= DECIDE_CONTEXT_MIN]
    print(f"catalog              {len(catalog):,} models")
    print(f"  with tool_use      {len(tool_models):,}")
    print(f"  ...on our plan     {len(on_plan):,}")
    print(f"  ...and >= {DECIDE_CONTEXT_MIN // 1024}k ctx   {len(roomy):,}   <- the field the choice is made from")
    print()

    # Model -> the configs that put it in a trading or reviewing seat.
    wanted: dict[str, list[str]] = {}
    research: dict[str, bool] = {}
    for path in CONFIGS if args.all_configs else (args.config,):
        config = load_config(Path(path), overrides_path=Path("/nonexistent"))
        uses_research = bool(config.get("research_tools_enabled"))
        for role, model in (
            ("trades", config.get("model")),
            ("reviews", config.get("review_model") or _first_other(config)),
        ):
            if not model:
                continue
            wanted.setdefault(model, []).append(f"{Path(path).name}:{role}")
            # A reviewer gets a digest, not the research loop.
            research[model] = research.get(model, False) or (uses_research and role == "trades")
    configured = set(wanted)
    for model in args.model:
        wanted.setdefault(model, []).append("--model")
        research.setdefault(model, False)
        configured.add(model)
    if args.rejected:
        for model in REJECTED:
            wanted.setdefault(model, []).append("rejected (strategy.md)")
            research.setdefault(model, False)

    header = f"{'model':<42} {'tools':<5} {'context':>7} {'plan':<5} {'$in/$out per M':<22} loaded"
    print(header)
    print("-" * len(header))
    failures = 0
    for model, roles in wanted.items():
        entry = by_id.get(model)
        detail = fetch_one(key, model) if entry else None
        print(_row(model, entry, detail))
        print(f"{'':<42} {', '.join(roles)}")
        tier = ((detail or {}).get("availability") or {}).get("tier")
        problems, notes = _verdict(model, entry, research.get(model, False))
        if tier and tier not in ("hot", "warm"):
            # Not a failure - it answers, eventually. But the first call pays
            # the load, and a cycle has request_timeout_sec to spend.
            notes.append(f"tier '{tier}': not loaded, so the next call cold-starts it")
        for problem in problems:
            print(f"{'':<42} FAILS: {problem}")
            failures += model in configured
        for note in notes:
            print(f"{'':<42} note:  {note}")

    failures += _check_prices(args, by_id)
    print()
    print(
        "Mechanical gates only. Instruction adherence - the criterion that actually\n"
        "discriminates - is measured with the real prompt on a challenger account."
    )
    return 1 if failures else 0


def _check_prices(args, by_id: dict) -> int:
    """`model_prices` in config is what the dashboard's dropdown costs and what
    eod_review.py bills the day at. Drift against the catalog is silent and
    misreports spend rather than failing anything, so check it here."""
    print()
    print("model_prices vs the catalog")
    failures = 0
    for path in CONFIGS if args.all_configs else (args.config,):
        for model, priced in (load_config(Path(path), overrides_path=Path("/nonexistent")).get("model_prices") or {}).items():
            live = (by_id.get(model) or {}).get("pricing")
            if live is None:
                print(f"  {Path(path).name}: {model} is priced but not in the catalog")
                failures += 1
            elif (priced.get("in"), priced.get("out")) != (live["input"], live["output"]):
                print(
                    f"  {Path(path).name}: {model} priced ${priced.get('in')}/${priced.get('out')}, "
                    f"catalog says ${live['input']}/${live['output']}"
                )
                failures += 1
    if not failures:
        print("  all configured prices match")
    return failures


def _first_other(config: dict) -> str | None:
    from bot.config import resolve_review_model

    return resolve_review_model(config)


if __name__ == "__main__":
    sys.exit(main())

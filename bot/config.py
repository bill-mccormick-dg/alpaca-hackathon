"""Loads config.yaml and applies runtime overrides. Pure: no credentials,
no network, safe to call from anywhere including tests.

Two layers, explicit precedence: config.yaml (git) is the base; active
entries in logs/overrides.yaml (bot/overrides.py - allowlisted keys, expire
end of day) win. Merged fresh on every call, i.e. every cron cycle.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import yaml

from bot import overrides as _overrides

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.yaml"

# The knobs whose effective values get journaled every cycle (bot/journal
# `config` event) so a P&L change can be attributed to a config change.
# strategy_notes is hashed, not dumped, to keep the record small.
TRACKED_KEYS = (
    "model", "temperature", "max_tokens", "research_contracts_per_underlying",
    "option_strike_band_pct", "stop_loss_pct", "take_profit_pct",
    "expiry_close_dte", "eod_close_dte", "final_flatten_date", "underlyings",
    "max_position_usd", "max_positions", "max_contracts_per_order",
    "daily_loss_cutoff_pct", "min_days_to_expiration", "max_days_to_expiration",
    # In the hash on purpose, unlike review_model below: the prior changes
    # the prompt, so flipping it must show up as a config change when a P&L
    # swing is being attributed.
    "predictions_enabled",
    # Same logic (#211): the instrument framing is prompt text, and the mixed
    # variant's whole experiment rides on it.
    "instrument_note",
    # The churn guard's thresholds (#132/#138): they change when a model
    # exit is allowed, so they belong in the hash - and the dashboard's
    # number rows read them from config/effective, which is this dict.
    "min_hold_minutes", "early_exit_drawdown_pct",
)


def load_config(
    path: Path | None = None,
    now: datetime | None = None,
    overrides_path: Path | None = None,
) -> dict:
    """`path` defaults to config.yaml; `--config config-<name>.yaml` on the
    entrypoints selects a variant. Overrides come from the account's file
    (bot/overrides.py::use_account) unless overrides_path says otherwise."""
    with open(path or CONFIG_FILE) as f:
        config = yaml.safe_load(f) or {}
    config["_config_file"] = str(path or CONFIG_FILE)
    active = _overrides.active_overrides(now, path=overrides_path)
    for key, entry in active.items():
        config[key] = entry["value"]
    # Provenance rides along under a key no YAML config would use, so callers
    # that want it don't need a second lookup and callers that don't never see
    # it in the values they care about.
    config["_overrides"] = {k: {"value": v["value"], "until": v.get("until"), "set_by": v.get("set_by")} for k, v in active.items()}
    return config


def review_choice(config: dict, traded=()) -> tuple[str | None, str | None]:
    """(reviewer, refused pin). Which model critiques the day in eod_review.py.

    The rule is one line: the reviewer is never a model that traded the day.
    `traded` is the set of models the journal says actually decided - every
    model in the digest's per-model rows - and the account's current `model`
    is always in the excluded set too.

    An explicit `review_model` (config or a runtime override) is honoured only
    when it passes that rule. Before #218 a pin returned before any
    comparison, so both challenger configs could pin the model `official`
    trades and nothing would notice if either account moved onto it - and
    for thirty seconds on 2026-09-02, `test` was configured exactly so. A pin
    that names a model that traded is refused and reported as the second
    element, so the digest and the journal can say it was ignored.

    With no usable pin, walk `review_model_preference` in order and take the
    first entry not in the excluded set. Recomputed on every call rather than
    resolved once and stored, because the trading model is changeable at
    runtime from the dashboard; freezing the choice would let someone switch
    the account onto the review model and silently lose the independence.

    Why `traded` and not just `model`: runtime overrides expire at 16:00 ET
    and the review runs at 16:05 ET, so an account that traded all day on an
    overridden model used to be checked against the git config it never ran.

    Returns (None, pin) when nothing is left to pick; the caller falls back to
    the trading model - a same-model review is worth more than no review."""
    trading = config.get("model")
    excluded = {str(m) for m in (trading, *traded) if m}
    explicit = config.get("review_model")
    refused = None
    if explicit:
        if str(explicit) not in excluded:
            return str(explicit), None
        refused = str(explicit)
    for candidate in config.get("review_model_preference") or []:
        if candidate and str(candidate) not in excluded:
            return str(candidate), refused
    return None, refused


def resolve_review_model(config: dict, traded=()) -> str | None:
    """The reviewer alone - see review_choice(). `traded` defaults to empty
    for the config event and the dashboard, which know only the current
    model; eod_review.py passes the journal's models."""
    return review_choice(config, traded)[0]


def model_params_for(config: dict, model: str | None) -> dict:
    """The extra request-body params for one model: the global `model_params`
    dict, with `model_params_by_model[<model>]` winning key-by-key on top.

    Exists because one toggle does not fit every model (#206): the global
    `enable_thinking: false` is load-bearing for Kimi-K2.6 and
    Qwen3.8-Flash-Next (without it they spend the whole token budget on
    hidden reasoning and forfeit the cycle), but Kimi-K3 REFUSES tool calls
    while thinking is disabled - verified live on Featherless, where it
    answers "tool usage is currently disabled" in prose instead of calling.
    Resolved at call time against the EFFECTIVE model, because the model is
    switchable from the dashboard mid-session and a per-model param frozen
    at startup would follow the wrong model.

    The merge is shallow and per top-level key on purpose: an override of
    `chat_template_kwargs` replaces the whole nested dict, so what is sent
    for a model is readable straight off its config entry rather than the
    result of a deep merge nobody can see."""
    merged: dict = {}
    base = config.get("model_params")
    if isinstance(base, dict):
        merged.update(base)
    per_model = config.get("model_params_by_model")
    if isinstance(per_model, dict) and model:
        override = per_model.get(str(model))
        if isinstance(override, dict):
            merged.update(override)
    return merged


def config_provenance(config: dict) -> dict:
    """What to journal: effective tracked values, a hash of them (plus the
    notes), the notes' first line + hash, and the active overrides."""
    tracked = {k: config.get(k) for k in TRACKED_KEYS}
    notes = str(config.get("strategy_notes") or "")
    notes_sha = hashlib.sha256(notes.encode()).hexdigest()[:12]
    digest = hashlib.sha256(json.dumps({**tracked, "strategy_notes": notes}, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return {
        **tracked,
        "strategy_notes_sha": notes_sha,
        "strategy_notes_head": notes.strip().splitlines()[0][:120] if notes.strip() else "",
        "config_hash": digest,
        # The RESOLVED review model, not the raw key: this is what
        # config/effective publishes, so the dashboard's selector shows the
        # model that will actually critique the day rather than a blank when
        # the key is unset. Deliberately not in TRACKED_KEYS - it does not
        # affect trading, so it should not churn config_hash.
        "review_model": resolve_review_model(config),
        "config_file": config.get("_config_file"),
        "overrides": config.get("_overrides", {}),
    }

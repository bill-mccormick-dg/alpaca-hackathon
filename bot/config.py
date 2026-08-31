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


def resolve_review_model(config: dict) -> str | None:
    """Which model critiques the day in eod_review.py.

    An explicit `review_model` (config or a runtime override) always wins. With
    none set, walk `review_model_preference` in order and take the first entry
    that is NOT the model that traded the day - a model grading its own
    reasoning is the weakest form of review, and the whole point of the key is
    that the critique comes from somewhere else.

    Recomputed on every call rather than resolved once and stored, because the
    trading model is changeable at runtime from the dashboard. Freezing the
    choice would let someone switch the account onto the review model and
    silently lose the independence this exists to provide.

    Returns None when there is nothing to pick, and the caller falls back to the
    trading model - a same-model review is worth more than no review."""
    explicit = config.get("review_model")
    if explicit:
        return str(explicit)
    trading = config.get("model")
    for candidate in config.get("review_model_preference") or []:
        if candidate and candidate != trading:
            return str(candidate)
    return None


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

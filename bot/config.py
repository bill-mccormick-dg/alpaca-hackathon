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
)


def load_config(
    path: Path = CONFIG_FILE,
    now: datetime | None = None,
    overrides_path: Path = _overrides.OVERRIDES_FILE,
) -> dict:
    with open(path) as f:
        config = yaml.safe_load(f) or {}
    active = _overrides.active_overrides(now, path=overrides_path)
    for key, entry in active.items():
        config[key] = entry["value"]
    # Provenance rides along under a key no YAML config would use, so callers
    # that want it don't need a second lookup and callers that don't never see
    # it in the values they care about.
    config["_overrides"] = {k: {"value": v["value"], "until": v.get("until"), "set_by": v.get("set_by")} for k, v in active.items()}
    return config


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
        "overrides": config.get("_overrides", {}),
    }

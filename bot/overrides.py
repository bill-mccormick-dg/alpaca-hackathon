"""Runtime config overrides - the ONE writer/reader for logs/overrides.yaml.

Two config layers, explicit precedence: config.yaml (git) is the base;
this file (runtime state on the CT, never in git) wins for an allowlisted
set of keys. Everything that changes a value at runtime - the override.py
CLI now, the MQTT/Home Assistant bridge later (issue #14) - goes through
set_override()/clear_override() here, so there is exactly one place that
validates and one file that can be inspected.

Why it never fights config.yaml:
- The layers never edit each other. A PR changes git; an override changes
  this file; bot/config.py::load_config() merges at read time, every cycle.
- Overrides EXPIRE - by default at the end of the trading day (16:00 ET).
  Intraday tweaks come from here; durable changes come from a PR; tomorrow
  always starts from git.
- Only strategy/model/exit knobs are overridable. Hard risk caps (position
  size, position count, DTE window, trading window, whitelist, daily-loss
  cutoff) are git-only on purpose: a bad payload must not be able to raise
  a cap mid-day.
- What the bot actually ran with is journaled every cycle (the `config`
  event) - nothing changes silently.
"""

import os
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

from bot.risk import EASTERN, LOGS_DIR

OVERRIDES_FILE = LOGS_DIR / "overrides.yaml"
END_OF_TRADING_DAY = time(16, 0)


def _float_in(lo: float, hi: float):
    def parse(value):
        v = float(value)
        if not (lo <= v <= hi):
            raise ValueError(f"must be between {lo} and {hi}, got {v}")
        return v

    return parse


def _int_in(lo: int, hi: int):
    def parse(value):
        v = int(float(value))
        if not (lo <= v <= hi):
            raise ValueError(f"must be between {lo} and {hi}, got {v}")
        return v

    return parse


def _nonempty_str(value):
    v = str(value).strip()
    if not v:
        raise ValueError("must not be empty")
    return v


# key -> parser/validator. Lenient in (strings from a CLI or an MQTT payload
# are fine), validated before anything is written.
OVERRIDABLE_KEYS = {
    "model": _nonempty_str,
    "temperature": _float_in(0.0, 2.0),
    "max_tokens": _int_in(50, 8000),
    "strategy_notes": _nonempty_str,
    "research_contracts_per_underlying": _int_in(1, 60),
    "option_strike_band_pct": _float_in(0.01, 0.5),
    "stop_loss_pct": _float_in(1, 100),
    "take_profit_pct": _float_in(1, 1000),
    "eod_close_dte": _int_in(0, 45),
}


def validate(key: str, value):
    """Coerce + range-check a value for `key`. Raises ValueError (unknown key
    or bad value) with a message fit to show a human or echo back over MQTT."""
    if key not in OVERRIDABLE_KEYS:
        raise ValueError(f"{key!r} is not runtime-overridable (allowed: {', '.join(sorted(OVERRIDABLE_KEYS))})")
    try:
        return OVERRIDABLE_KEYS[key](value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key}: {exc}") from None


def default_until(now: datetime | None = None) -> datetime:
    """End of today's trading day, Eastern. If it's already past 16:00 ET
    (an evening tweak for tomorrow), the override lasts through the next
    calendar day's close - the operator said 'tomorrow', so honour it."""
    now = now or datetime.now(EASTERN)
    until = now.replace(hour=END_OF_TRADING_DAY.hour, minute=END_OF_TRADING_DAY.minute, second=0, microsecond=0)
    if now >= until:
        until += timedelta(days=1)
    return until


def _read_raw(path: Path = OVERRIDES_FILE) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(data: dict, path: Path = OVERRIDES_FILE) -> None:
    """Atomic: a cron cycle reading mid-write must see the old file or the
    new one, never a truncated one."""
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".overrides.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_active(entry: dict, now: datetime) -> bool:
    until = entry.get("until")
    if not until:
        return True
    try:
        until_dt = datetime.fromisoformat(str(until))
    except ValueError:
        return False
    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=EASTERN)
    return now < until_dt


def active_overrides(now: datetime | None = None, path: Path = OVERRIDES_FILE) -> dict:
    """{key: {"value", "until", "set_by", "set_at"}} for every unexpired,
    allowlisted entry. Expired or non-allowlisted entries are ignored here
    and pruned on the next write."""
    now = now or datetime.now(EASTERN)
    out = {}
    for key, entry in _read_raw(path).items():
        if key not in OVERRIDABLE_KEYS or not isinstance(entry, dict) or "value" not in entry:
            continue
        if not _is_active(entry, now):
            continue
        try:
            value = validate(key, entry["value"])
        except ValueError:
            continue  # a hand-edited bad value must not take the bot down
        out[key] = {**entry, "value": value}
    return out


def set_override(
    key: str,
    value,
    until: datetime | None = None,
    set_by: str = "cli",
    now: datetime | None = None,
    path: Path = OVERRIDES_FILE,
) -> dict:
    """Validate, then write. Returns the stored entry."""
    now = now or datetime.now(EASTERN)
    clean = validate(key, value)
    until = until or default_until(now)
    if until.tzinfo is None:
        until = until.replace(tzinfo=EASTERN)
    entry = {
        "value": clean,
        "until": until.isoformat(timespec="minutes"),
        "set_by": set_by,
        "set_at": now.isoformat(timespec="seconds"),
    }
    data = {k: v for k, v in _read_raw(path).items() if k in OVERRIDABLE_KEYS and isinstance(v, dict) and _is_active(v, now)}
    data[key] = entry
    _write_raw(data, path)
    return entry


def clear_override(key: str, path: Path = OVERRIDES_FILE) -> bool:
    data = _read_raw(path)
    existed = key in data
    data.pop(key, None)
    _write_raw(data, path)
    return existed


def clear_all(path: Path = OVERRIDES_FILE) -> int:
    data = _read_raw(path)
    _write_raw({}, path)
    return len(data)

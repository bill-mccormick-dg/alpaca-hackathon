"""Credential resolution for the hackathon bot.

Two Alpaca paper accounts exist (see README.md "Account"): the OFFICIAL
account (judging, zero orders until Mon Aug 31 9:30 AM ET) and a TEST
account (safe to trade on for all development). `load_credentials()`
defaults to "test" — official credentials require explicitly asking for
them, so an accidental order can't land on the judging account.

Priority: environment variables already set, then the matching credentials
file the `alpaca-hackathon` Ansible role deploys on CT 108, then (test
account only) a local `.env` for dev machines. Mirrors alpaca-trader's
`trader/broker.py:_load_credentials()` fallback-chain shape, adapted to
this project's four required values, two accounts, and deployed paths.
"""

import os
import re
from pathlib import Path

REQUIRED_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_BASE_URL",
    "FEATHERLESS_API_KEY",
)

# Matches homenetwork/ansible/roles/alpaca-hackathon/templates/*.env.j2
PRODUCTION_CREDENTIALS_DIR = Path("/root/.config/alpaca-hackathon")
OFFICIAL = "official"

# Local dev fallback, gitignored. Any NON-official account may resolve from
# a local file (.env.<name>, then .env) - a teammate's compose stack or the
# N-variant experiment farm. The official account never resolves from a
# local file, only the deployed path or explicit env vars.
REPO_ROOT = Path(__file__).resolve().parent.parent
DOTENV_FILE = REPO_ROOT / ".env"

_NAME_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def credentials_file(account: str) -> Path:
    """Deployed path for an account: credentials.env for official,
    credentials-<name>.env for everything else."""
    return PRODUCTION_CREDENTIALS_DIR / ("credentials.env" if account == OFFICIAL else f"credentials-{account}.env")


def validate_account(account: str) -> str:
    if not isinstance(account, str) or not _NAME_OK.match(account):
        raise ValueError(f"Bad account name {account!r}: lowercase letters, digits, - and _ only (e.g. test, official, qwen-a)")
    return account


def _parse_env_file(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _merge_from(values: dict, path: Path) -> None:
    parsed = _parse_env_file(path)
    for key in REQUIRED_KEYS:
        if key not in values and parsed.get(key):
            values[key] = parsed[key]


def load_credentials(account: str = "test") -> dict:
    """Resolve all four required credentials for a named account, or raise
    RuntimeError naming exactly which are missing and where this looked.

    Order: environment variables -> the deployed file for the account ->
    (non-official only) local .env.<account> -> local .env."""
    validate_account(account)
    values = {k: os.environ[k] for k in REQUIRED_KEYS if os.environ.get(k)}
    checked = ["environment variables"]

    production_file = credentials_file(account)
    checked.append(str(production_file))
    if len(values) < len(REQUIRED_KEYS) and production_file.exists():
        _merge_from(values, production_file)

    if account != OFFICIAL:
        for local in (DOTENV_FILE.with_name(f".env.{account}"), DOTENV_FILE):
            checked.append(str(local))
            if len(values) < len(REQUIRED_KEYS) and local.exists():
                _merge_from(values, local)

    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise RuntimeError(
            f"Missing credentials for account={account!r}: {', '.join(missing)}. Checked {', '.join(checked)}."
        )
    return values

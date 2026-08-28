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
from pathlib import Path

REQUIRED_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_BASE_URL",
    "FEATHERLESS_API_KEY",
)

# Matches homenetwork/ansible/roles/alpaca-hackathon/templates/*.env.j2
PRODUCTION_CREDENTIALS_DIR = Path("/root/.config/alpaca-hackathon")
ACCOUNT_FILES = {
    "test": "credentials-test.env",
    "official": "credentials.env",
}

# Local dev fallback, gitignored. Test account only — official credentials
# never resolve from a local file, only the deployed path or explicit env vars.
DOTENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _parse_env_file(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_credentials(account: str = "test") -> dict:
    """Resolve all four required credentials for `account` ("test" or
    "official"), or raise RuntimeError naming exactly which are missing and
    where this looked for them."""
    if account not in ACCOUNT_FILES:
        raise ValueError(f"Unknown account {account!r}; expected one of {sorted(ACCOUNT_FILES)}")

    values = {k: os.environ[k] for k in REQUIRED_KEYS if os.environ.get(k)}

    production_file = PRODUCTION_CREDENTIALS_DIR / ACCOUNT_FILES[account]
    if len(values) < len(REQUIRED_KEYS) and production_file.exists():
        parsed = _parse_env_file(production_file)
        for key in REQUIRED_KEYS:
            if key not in values and parsed.get(key):
                values[key] = parsed[key]

    if account == "test" and len(values) < len(REQUIRED_KEYS) and DOTENV_FILE.exists():
        from dotenv import dotenv_values

        parsed = dotenv_values(DOTENV_FILE)
        for key in REQUIRED_KEYS:
            if key not in values and parsed.get(key):
                values[key] = parsed[key]

    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        checked = f"environment variables, {production_file}"
        if account == "test":
            checked += f", and {DOTENV_FILE}"
        raise RuntimeError(
            f"Missing credentials for account={account!r}: {', '.join(missing)}. Checked {checked}."
        )
    return values

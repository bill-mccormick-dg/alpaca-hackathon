"""Credential resolution for the hackathon bot.

Priority: environment variables already set, then the credentials file the
`alpaca-hackathon` Ansible role deploys on CT 108, then a local `.env` for
dev machines. Mirrors alpaca-trader's `trader/broker.py:_load_credentials()`
fallback-chain shape, adapted to this project's four required values and
deployed path.
"""

import os
from pathlib import Path

REQUIRED_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_BASE_URL",
    "FEATHERLESS_API_KEY",
)

# Matches homenetwork/ansible/roles/alpaca-hackathon/templates/credentials.env.j2
PRODUCTION_CREDENTIALS_FILE = Path("/root/.config/alpaca-hackathon/credentials.env")

# Local dev fallback, gitignored.
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


def load_credentials() -> dict:
    """Resolve all four required credentials, or raise RuntimeError naming
    exactly which are missing and where this looked for them."""
    values = {k: os.environ[k] for k in REQUIRED_KEYS if os.environ.get(k)}

    if len(values) < len(REQUIRED_KEYS) and PRODUCTION_CREDENTIALS_FILE.exists():
        parsed = _parse_env_file(PRODUCTION_CREDENTIALS_FILE)
        for key in REQUIRED_KEYS:
            if key not in values and parsed.get(key):
                values[key] = parsed[key]

    if len(values) < len(REQUIRED_KEYS) and DOTENV_FILE.exists():
        from dotenv import dotenv_values

        parsed = dotenv_values(DOTENV_FILE)
        for key in REQUIRED_KEYS:
            if key not in values and parsed.get(key):
                values[key] = parsed[key]

    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise RuntimeError(
            "Missing credentials: {}. Checked environment variables, {}, and {}.".format(
                ", ".join(missing), PRODUCTION_CREDENTIALS_FILE, DOTENV_FILE
            )
        )
    return values

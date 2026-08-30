"""Credential resolution for the hackathon bot.

Two Alpaca paper accounts exist (see README.md "Account"): the OFFICIAL
account (judging, zero orders until Mon Aug 31 9:30 AM ET) and a TEST
account (safe to trade on for all development). `load_credentials()`
defaults to "test" — official credentials require explicitly asking for
them, so an accidental order can't land on the judging account.

Priority: environment variables already set, then the matching credentials
file the `alpaca-hackathon` Ansible role deploys on CT 108, then (non-official
accounts only) a local `secrets.yaml`, then the legacy `.env.<name>` / `.env`.
Mirrors alpaca-trader's `trader/broker.py:_load_credentials()` fallback-chain
shape, adapted to this project's four required values, two accounts, and
deployed paths.

`secrets.yaml` (gitignored; `secrets.example.yaml` is the committed template)
carries every local account in one file, which `.env` could not: a `.env` says
nothing on its face about which account its keys belong to, so the real values -
which live in the private homenetwork repo's ansible vault, under
`vault_alpaca_hackathon_test_*` - got hand-copied into an anonymous file with
no way to tell right from wrong afterwards. secrets.example.yaml documents the
vault variable behind each field.
"""

import os
import re
from pathlib import Path

import yaml

REQUIRED_KEYS = (
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_BASE_URL",
    "FEATHERLESS_API_KEY",
)

# Optional, and resolved from the same files as the required keys above -
# see load_mqtt_env(). Their absence disables the MQTT side channel rather
# than failing anything.
MQTT_KEYS = ("MQTT_HOST", "MQTT_PORT", "MQTT_USERNAME", "MQTT_PASSWORD")

# Matches homenetwork/ansible/roles/alpaca-hackathon/templates/*.env.j2
PRODUCTION_CREDENTIALS_DIR = Path("/root/.config/alpaca-hackathon")
OFFICIAL = "official"

# Local dev fallback, gitignored. Any NON-official account may resolve from a
# local file (secrets.yaml, then the legacy .env.<name> / .env) - a teammate's
# compose stack or the N-variant experiment farm. The official account never
# resolves from a local file, only the deployed path or explicit env vars.
REPO_ROOT = Path(__file__).resolve().parent.parent
SECRETS_FILE = REPO_ROOT / "secrets.yaml"
DOTENV_FILE = REPO_ROOT / ".env"

# secrets.yaml field -> the flat env-style key the rest of the code expects.
# Each may appear inside an account block or at the top level as a default
# shared by every account; the account's own value wins.
SECRET_FIELDS = {
    "api_key": "ALPACA_API_KEY",
    "secret_key": "ALPACA_SECRET_KEY",
    "base_url": "ALPACA_BASE_URL",
    "featherless_api_key": "FEATHERLESS_API_KEY",
}
MQTT_FIELDS = {
    "host": "MQTT_HOST",
    "port": "MQTT_PORT",
    "username": "MQTT_USERNAME",
    "password": "MQTT_PASSWORD",
}

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


def _merge(values: dict, parsed: dict, keys=REQUIRED_KEYS) -> None:
    """Fill in any of `keys` that `values` is still missing. Blank and None
    count as absent, so an unedited secrets.example.yaml behaves exactly like
    no file at all rather than failing later as a bad-credentials error."""
    for key in keys:
        if key not in values and parsed.get(key):
            values[key] = parsed[key]


def _merge_from(values: dict, path: Path) -> None:
    _merge(values, _parse_env_file(path))


def _readable_file(path: Path) -> bool:
    """Path.exists() propagates EACCES rather than returning False, so an
    unreadable deployed directory - /root/.config/... seen from a non-root
    container - turned `--account official` on a dev box into a confusing
    PermissionError instead of the intended "missing credentials" message."""
    try:
        return path.is_file()
    except OSError:
        return False


def load_secrets(path: Path | None = None) -> dict:
    """Parse secrets.yaml, or return {} when there isn't one.

    Raises RuntimeError on malformed YAML, a non-mapping document, or an
    `official` entry anywhere in the file."""
    path = path or SECRETS_FILE
    # is_file(), not exists(): `docker compose` creates an empty DIRECTORY at
    # a bind-mount source that doesn't exist yet, and that must read as "no
    # secrets file" rather than blowing up every command with IsADirectoryError.
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Could not parse {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must be a mapping, got {type(data).__name__}.")  # noqa: TRY004 - a malformed config file is a user-facing error, not a TypeError; every secrets.yaml problem surfaces as RuntimeError

    accounts = data.get("accounts") or {}
    if not isinstance(accounts, dict):
        raise RuntimeError(f"{path}: `accounts` must be a mapping of name -> credentials.")  # noqa: TRY004 - a malformed config file is a user-facing error, not a TypeError; every secrets.yaml problem surfaces as RuntimeError

    # The judging account must never be resolvable from a local file. Rejecting
    # the whole file - for every account, not just when loading `official` -
    # means pasting official keys in here fails loudly and immediately instead
    # of quietly trading the judging account under some other account's name.
    for name in (*data, *accounts):
        if str(name).strip().lower() == OFFICIAL:
            raise RuntimeError(
                f"{path} contains an '{OFFICIAL}' entry. The judging account's keys must "
                f"never live in a local file - they exist only in {credentials_file(OFFICIAL)} "
                f"on CT 108. Delete the entry."
            )
    for name in accounts:
        validate_account(str(name))
    return data


def secrets_for_account(account: str, path: Path | None = None) -> dict:
    """Flat {ENV_KEY: value} for one account out of secrets.yaml, the account's
    own block winning over the file-level shared defaults. Always {} for the
    official account, so even a caller that forgets the OFFICIAL branch cannot
    leak the judging account's credentials out of a local file."""
    validate_account(account)
    if account == OFFICIAL:
        return {}
    data = load_secrets(path)
    if not data:
        return {}
    block = (data.get("accounts") or {}).get(account) or {}
    if not isinstance(block, dict):
        raise RuntimeError(f"{path or SECRETS_FILE}: `accounts.{account}` must be a mapping.")  # noqa: TRY004 - a malformed config file is a user-facing error, not a TypeError; every secrets.yaml problem surfaces as RuntimeError

    values = {}
    for field, key in SECRET_FIELDS.items():
        value = block.get(field) if block.get(field) is not None else data.get(field)
        if value is not None and str(value) != "":
            values[key] = str(value)
    shared_mqtt = data.get("mqtt") or {}
    account_mqtt = block.get("mqtt") or {}
    for field, key in MQTT_FIELDS.items():
        value = account_mqtt.get(field) if account_mqtt.get(field) is not None else shared_mqtt.get(field)
        if value is not None and str(value) != "":
            values[key] = str(value)
    return values


def load_credentials(account: str = "test") -> dict:
    """Resolve all four required credentials for a named account, or raise
    RuntimeError naming exactly which are missing and where this looked.

    Order: environment variables -> the deployed file for the account ->
    (non-official only) local secrets.yaml -> legacy .env.<account> -> .env."""
    validate_account(account)
    values = {k: os.environ[k] for k in REQUIRED_KEYS if os.environ.get(k)}
    checked = ["environment variables"]

    production_file = credentials_file(account)
    checked.append(str(production_file))
    if len(values) < len(REQUIRED_KEYS) and _readable_file(production_file):
        _merge_from(values, production_file)

    if account != OFFICIAL:
        # secrets.yaml sits above the legacy .env files, so a stale .env left
        # on someone's disk can never silently beat the canonical file.
        checked.append(str(SECRETS_FILE))
        if len(values) < len(REQUIRED_KEYS):
            _merge(values, secrets_for_account(account))
        for local in (DOTENV_FILE.with_name(f".env.{account}"), DOTENV_FILE):
            checked.append(str(local))
            if len(values) < len(REQUIRED_KEYS) and _readable_file(local):
                _merge_from(values, local)

    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise RuntimeError(
            f"Missing credentials for account={account!r}: {', '.join(missing)}. Checked {', '.join(checked)}."
        )
    return values


def load_mqtt_env(account: str = "test") -> dict:
    """Broker settings for the MQTT side channel, from the SAME file chain
    as load_credentials (the deployed credentials file already carries
    them alongside the API keys).

    This exists because cron is the thing that actually runs the bot, and
    a cron job inherits almost no environment - only what /etc/cron.d sets
    (PATH, PYTHONDONTWRITEBYTECODE). load_credentials returns the four
    REQUIRED_KEYS as a dict and never exports anything, so bot/mqtt.py's
    os.environ lookup found no MQTT_HOST under cron and silently disabled
    itself: every scheduled cycle traded correctly and published nothing.
    Verified live with `env -i` before this was added.

    Optional by design: returns whatever it finds and never raises. No
    broker configured must stay a no-op, not an error - the side channel
    may never break a trading cycle."""
    try:
        validate_account(account)
    except ValueError:
        return {}
    found = {k: os.environ[k] for k in MQTT_KEYS if os.environ.get(k)}

    def _from_secrets() -> dict:
        # A malformed or `official`-carrying secrets.yaml must disable the side
        # channel, not raise: load_credentials will report it properly, and a
        # broker problem may never break a trading cycle.
        try:
            return secrets_for_account(account)
        except (RuntimeError, ValueError, OSError):
            return {}

    sources = [lambda: _parse_env_file(credentials_file(account))]
    if account != OFFICIAL:
        sources.append(_from_secrets)
        sources += [
            (lambda p=p: _parse_env_file(p))
            for p in (DOTENV_FILE.with_name(f".env.{account}"), DOTENV_FILE)
        ]
    for source in sources:
        if len(found) == len(MQTT_KEYS):
            break
        try:
            _merge(found, source(), MQTT_KEYS)
        except OSError:
            continue
    return found

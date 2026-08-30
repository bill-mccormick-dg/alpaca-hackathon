"""Does the account we authenticated to match the account we were asked for?

Every other guard in this codebase keys on the account NAME - the `--account`
string, the halt-file suffixes, `run_cycle.py`'s pre-window refusal. None of
them look at the credentials themselves. So the judging account's keys reaching
a non-official name - a mis-copied block in secrets.yaml, a stray ALPACA_API_KEY
in the environment, the wrong vault variable - would trade `PA3VS39Y5LE2` while
every name-based check reported "test" and every log line agreed. This module is
the one check that keys on what the broker actually says the account is.

The policy is deliberately asymmetric, weighted by what each failure costs:

  - A NON-official run resolving to the judging account is the catastrophic
    case, and refusing costs nothing but a skipped challenger cycle. It is
    fail-CLOSED: an unverifiable account number refuses too.
  - The OFFICIAL run is what the competition depends on. A confirmed mismatch
    still refuses, but an UNVERIFIABLE account number - a changed API shape, a
    field that stopped being returned - must not take the judged account out of
    the market on the strength of a parsing regression. It warns and proceeds.

The account number is public (README "Account"), so hardcoding it leaks nothing
and cannot be silently edited the way a config value could.
"""

OFFICIAL = "official"
OFFICIAL_ACCOUNT_NUMBER = "PA3VS39Y5LE2"


def _normalize(account_name: str | None, account_number: str | None) -> tuple[str, str]:
    return (account_name or "").strip().lower(), (account_number or "").strip()


def identity_mismatch(account_name: str | None, account_number: str | None) -> str:
    """The refusal reason when the broker's account number is KNOWN and wrong,
    otherwise "".

    An unknown number is not a mismatch here on purpose: this is the funnel's
    last-line check (see `bot/risk.py::check_order`), and the entrypoints have
    already fail-closed on "unknown" via `check_account_identity` before any
    order is proposed. Keeping the two separate is what lets the order path stay
    strict without every unit test having to synthesize an account number."""
    name, number = _normalize(account_name, account_number)
    if not number:
        return ""
    if name == OFFICIAL and number != OFFICIAL_ACCOUNT_NUMBER:
        return (
            f"account mismatch: --account {OFFICIAL} resolved credentials for account "
            f"{number}, not the judging account {OFFICIAL_ACCOUNT_NUMBER}"
        )
    if name != OFFICIAL and number == OFFICIAL_ACCOUNT_NUMBER:
        return (
            f"account mismatch: --account {name or '(unset)'} resolved credentials for the "
            f"JUDGING account {OFFICIAL_ACCOUNT_NUMBER} - check secrets.yaml and the deployed "
            f"credentials file, see README \"Where the keys come from\""
        )
    return ""


def check_account_identity(account_name: str | None, account_number: str | None) -> tuple[bool, str]:
    """(allowed, message) for an entrypoint, applying the full policy above.

    `message` is "" only when the identity is positively confirmed; a non-empty
    message with allowed=True is the official account's unverified warning,
    which the caller should print and journal but not act on."""
    name, number = _normalize(account_name, account_number)

    mismatch = identity_mismatch(account_name, account_number)
    if mismatch:
        return False, f"refusing: {mismatch}"

    if not number:
        if name == OFFICIAL:
            return True, (
                f"WARNING: could not verify the broker account number for --account {OFFICIAL}. "
                f"Proceeding anyway - a parsing regression must not halt the judged account - "
                f"but confirm this is {OFFICIAL_ACCOUNT_NUMBER}."
            )
        return False, (
            f"refusing: could not verify the broker account number for --account "
            f"{name or '(unset)'}, so there is no proof these credentials are not the judging "
            f"account {OFFICIAL_ACCOUNT_NUMBER}"
        )

    return True, ""

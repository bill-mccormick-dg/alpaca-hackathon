"""Retry policy for the model call, and only for the model call.

Measured on CT 108's journals before this was written (issue #85), over the
two accounts' first days:

    official (Kimi-K2-Instruct)      3 decide errors / 10 cycles   30%
      2x  upstream "no choices": {"code": "no_response", "type": "server_error"}
      1x  unrecorded detail (predates #77's error-body fix)

    test (Qwen3.8-Flash-Next)        5 decide errors / 28 cycles   18%
      5x  ValueError: no JSON array in model output: ''

Two different failure modes, split cleanly by model, and the second one is the
majority: a 200 OK with a valid body whose completion content is the empty
string. An httpx-level retry never sees that one, which is why this policy is
applied around the whole `decide()` call rather than inside the HTTP client.

`decide()` is safe to re-run: it builds its prompt from a snapshot already in
hand, and its research tool calls are read-only Alpaca queries. Re-running
costs latency and Featherless credit, nothing else - so attempts are capped
tightly, and every attempt is journalled rather than hidden.

What is deliberately NOT retried: anything that will fail again the same way.
Auth and quota failures must stay loud on a metered credit during a scored
week, and a truncated answer is a max_tokens problem that another call will
just reproduce at the same cost.
"""

import asyncio
import time

import httpx

# Substrings of an upstream error body that mean "try again", checked against
# the provider's own message. Seen live: {"message": "No successful response
# received from completion service", "type": "server_error", "code":
# "no_response"}.
TRANSIENT_UPSTREAM_MARKERS = (
    "no_response",
    "server_error",
    "overloaded",
    "temporarily unavailable",
    "timeout",
    "timed out",
    "try again",
)

# ...and the ones that mean "stop asking". A spent credit or a bad key retried
# three times is three times the noise and none of the answer.
PERMANENT_MARKERS = (
    "insufficient",
    "quota",
    "credit",
    "billing",
    "unauthorized",
    "invalid api key",
    "authentication",
    "forbidden",
    "not found",
    "does not exist",
)

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _mentions(text: str, markers) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def classify(exc: BaseException) -> str | None:
    """The reason this exception is worth retrying, or None to give up.

    Returning a reason string rather than a bool so the journal can say WHICH
    transient shape fired - the whole point of #85 is being able to tell
    upstream flakiness from a prompt that stopped working."""
    # Import here: bot.decide imports nothing from this module, and this keeps
    # the dependency one-way.
    from bot.decide import TruncatedOutput

    if isinstance(exc, TruncatedOutput):
        return None  # max_tokens is a config fix; another call truncates too

    if isinstance(exc, httpx.TimeoutException):
        return "http timeout"
    if isinstance(exc, httpx.TransportError):  # connect/read/write/protocol
        return f"transport error: {type(exc).__name__}"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in RETRYABLE_STATUS:
            return f"http {status}"
        return None

    text = str(exc)

    # decide() raises this when a 200 carried an error object instead of
    # choices. The provider's own message decides whether it is worth another
    # go - a spent credit is not.
    if isinstance(exc, RuntimeError) and "model returned no choices" in text:
        if _mentions(text, PERMANENT_MARKERS):
            return None
        if _mentions(text, TRANSIENT_UPSTREAM_MARKERS):
            return "upstream returned no choices"
        # An unrecognised error body: retry once rather than forfeit the whole
        # slot to a shape we have not catalogued yet. The attempt cap bounds
        # the cost of being wrong.
        return "upstream returned no choices (unrecognised body)"

    # The Qwen shape: HTTP 200, valid body, empty completion. Retry only when
    # the model genuinely produced nothing - non-empty output that fails to
    # parse is a prompt or format problem, and re-rolling it burns credit for
    # the same answer.
    if isinstance(exc, ValueError) and "no JSON array in model output" in text:
        if text.rstrip().endswith(("''", '""')):
            return "empty model output"
        return None

    return None


def delay_for(attempt: int, base: float = 2.0, cap: float = 8.0) -> float:
    """Backoff before attempt N (1-indexed): 2s, 4s, 8s, capped.

    No jitter on purpose - a single process making one call at a time has
    nothing to spread out, and a predictable delay makes the budget arithmetic
    in run_cycle.py checkable by hand."""
    return min(base * (2 ** max(attempt - 1, 0)), cap)


class RetryBudget:
    """Wall-clock guard so a retrying cycle can never still be running when the
    next one starts. Cron fires every 10 minutes; this must stay far inside it,
    because two overlapping cycles would both act on their own stale snapshot."""

    def __init__(self, max_attempts: int = 3, budget_sec: float = 120.0):
        self.max_attempts = max(1, int(max_attempts))
        self.budget_sec = float(budget_sec)
        self._started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def may_retry(self, attempt: int, delay: float) -> tuple[bool, str]:
        """Is another attempt allowed after `attempt` ones already failed?"""
        if attempt >= self.max_attempts:
            return False, f"attempt cap reached ({self.max_attempts})"
        if self.elapsed + delay >= self.budget_sec:
            return False, (
                f"retry budget spent ({self.elapsed:.1f}s of {self.budget_sec:.0f}s, "
                f"next wait {delay:.1f}s)"
            )
        return True, ""


async def call_with_retry(call, *, budget: RetryBudget, on_retry=None, on_giveup=None, sleep=None):
    """Await `call()`, retrying the transient shapes above within `budget`.

    `on_retry(attempt, reason, delay, exc)` and `on_giveup(attempt, reason, exc)`
    are for journalling; both are optional and synchronous. Re-raises the last
    exception when the budget or the policy says stop, so the caller's existing
    error handling is unchanged."""
    sleep = sleep or asyncio.sleep
    attempt = 0
    while True:
        attempt += 1
        try:
            return await call()
        # Broad by necessity: classify() is the policy, and anything it does
        # not recognise is re-raised untouched on the next line.
        except Exception as exc:
            reason = classify(exc)
            if reason is None:
                if on_giveup:
                    on_giveup(attempt, "not retryable", exc)
                raise
            delay = delay_for(attempt)
            allowed, stop_reason = budget.may_retry(attempt, delay)
            if not allowed:
                if on_giveup:
                    on_giveup(attempt, stop_reason, exc)
                raise
            if on_retry:
                on_retry(attempt, reason, delay, exc)
            await sleep(delay)


def summarize(exc: BaseException, limit: int = 200) -> str:
    """Short single-line description for a journal field."""
    text = str(exc).replace("\n", " ")
    return f"{type(exc).__name__}: {text[:limit]}" if text else type(exc).__name__


__all__ = [
    "RetryBudget",
    "call_with_retry",
    "classify",
    "delay_for",
    "summarize",
]

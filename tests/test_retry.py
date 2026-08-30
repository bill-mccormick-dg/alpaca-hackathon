"""Retry policy for the decide step (issue #85).

The classification cases below use the VERBATIM error strings observed on
CT 108's journals, so a change in policy has to confront the shapes that
actually happened rather than invented ones."""

import unittest

import httpx

from bot.decide import TruncatedOutput
from bot.retry import (
    RetryBudget,
    call_with_retry,
    classify,
    delay_for,
    summarize,
)

# Verbatim from logs/journal.jsonl on CT 108, 2026-08-30T00:31:23-04:00.
LIVE_NO_RESPONSE = RuntimeError(
    'model returned no choices: {"message": "No successful response received from '
    'completion service", "type": "server_error", "code": "no_response"}'
)
# Verbatim from logs/journal-test.jsonl, 5 occurrences 2026-08-29T10:57-11:00.
LIVE_EMPTY_OUTPUT = ValueError("no JSON array in model output: ''")


def _status_error(code):
    request = httpx.Request("POST", "https://api.featherless.ai/v1/chat/completions")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(code, request=request))


class ClassifyObservedFailuresTest(unittest.TestCase):
    """The two shapes that actually cost us cycles."""

    def test_upstream_no_response_is_retryable(self):
        self.assertEqual(classify(LIVE_NO_RESPONSE), "upstream returned no choices")

    def test_empty_model_output_is_retryable(self):
        self.assertEqual(classify(LIVE_EMPTY_OUTPUT), "empty model output")


class ClassifyPermanentFailuresTest(unittest.TestCase):
    """Retrying these three times is three times the noise and none of the
    answer - and on a metered credit during a scored week, real money."""

    def test_spent_credit_is_not_retryable(self):
        exc = RuntimeError('model returned no choices: {"message": "insufficient credits"}')

        self.assertIsNone(classify(exc))

    def test_quota_is_not_retryable(self):
        exc = RuntimeError('model returned no choices: {"message": "monthly quota exceeded"}')

        self.assertIsNone(classify(exc))

    def test_bad_key_is_not_retryable(self):
        exc = RuntimeError('model returned no choices: {"message": "invalid api key"}')

        self.assertIsNone(classify(exc))

    def test_truncated_output_is_not_retryable(self):
        """max_tokens is a config fix; another call truncates identically."""
        self.assertIsNone(classify(TruncatedOutput("output truncated (finish_reason=length)")))

    def test_non_empty_unparseable_output_is_not_retryable(self):
        """The model said something, it just wasn't JSON - a prompt problem,
        and re-rolling it buys the same answer at the same price."""
        exc = ValueError("no JSON array in model output: 'I think we should hold today.'")

        self.assertIsNone(classify(exc))

    def test_unrelated_exceptions_are_not_retryable(self):
        for exc in (KeyError("choices"), ZeroDivisionError(), ValueError("something else entirely")):
            self.assertIsNone(classify(exc))


class ClassifyHttpTest(unittest.TestCase):
    def test_timeouts_and_transport_errors_are_retryable(self):
        self.assertEqual(classify(httpx.ReadTimeout("slow")), "http timeout")
        self.assertIsNotNone(classify(httpx.ConnectError("refused")))

    def test_server_and_rate_limit_statuses_are_retryable(self):
        for code in (429, 500, 502, 503, 504):
            self.assertEqual(classify(_status_error(code)), f"http {code}")

    def test_client_errors_are_not_retryable(self):
        for code in (400, 401, 403, 404, 422):
            self.assertIsNone(classify(_status_error(code)))


class ClassifyUnknownUpstreamBodyTest(unittest.TestCase):
    def test_unrecognised_error_body_still_retries(self):
        """Better one extra call than forfeiting the slot to a shape we have
        not catalogued. The attempt cap bounds the cost of guessing wrong."""
        exc = RuntimeError('model returned no choices: {"message": "flurble"}')

        self.assertEqual(classify(exc), "upstream returned no choices (unrecognised body)")


class DelayTest(unittest.TestCase):
    def test_backoff_doubles_and_caps(self):
        self.assertEqual([delay_for(n) for n in (1, 2, 3, 4)], [2.0, 4.0, 8.0, 8.0])


class RetryBudgetTest(unittest.TestCase):
    def test_attempt_cap_stops_retrying(self):
        budget = RetryBudget(max_attempts=3, budget_sec=1000)

        self.assertTrue(budget.may_retry(1, 2.0)[0])
        self.assertTrue(budget.may_retry(2, 2.0)[0])
        allowed, reason = budget.may_retry(3, 2.0)

        self.assertFalse(allowed)
        self.assertIn("attempt cap", reason)

    def test_wall_clock_stops_retrying_even_under_the_attempt_cap(self):
        budget = RetryBudget(max_attempts=99, budget_sec=1.0)
        allowed, reason = budget.may_retry(1, 8.0)

        self.assertFalse(allowed)
        self.assertIn("budget spent", reason)

    def test_budget_stays_far_inside_the_ten_minute_cadence(self):
        """Cron fires every 10 minutes. Two overlapping cycles would each act
        on their own stale snapshot, so the shipped defaults must not come
        close - worst case is the budget plus one in-flight request."""
        budget = RetryBudget()
        worst_case = budget.budget_sec + 60  # + request_timeout_sec

        self.assertLess(worst_case, 600 / 2)

    def test_at_least_one_attempt_always_happens(self):
        self.assertEqual(RetryBudget(max_attempts=0).max_attempts, 1)


class CallWithRetryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.slept = []

    async def _sleep(self, seconds):
        self.slept.append(seconds)

    async def test_returns_immediately_when_the_call_succeeds(self):
        calls = []

        async def call():
            calls.append(1)
            return "decision"

        result = await call_with_retry(call, budget=RetryBudget(), sleep=self._sleep)

        self.assertEqual(result, "decision")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.slept, [])

    async def test_recovers_on_a_later_attempt(self):
        """The whole point: a cycle that would have been forfeited completes."""
        attempts = []

        async def call():
            attempts.append(1)
            if len(attempts) < 3:
                raise LIVE_NO_RESPONSE
            return "decision"

        result = await call_with_retry(call, budget=RetryBudget(), sleep=self._sleep)

        self.assertEqual(result, "decision")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.slept, [2.0, 4.0])

    async def test_gives_up_after_the_attempt_cap_and_reraises(self):
        async def call():
            raise LIVE_EMPTY_OUTPUT

        with self.assertRaises(ValueError):
            await call_with_retry(call, budget=RetryBudget(max_attempts=3), sleep=self._sleep)

        self.assertEqual(len(self.slept), 2)  # 3 attempts, 2 waits

    async def test_does_not_retry_a_permanent_failure(self):
        attempts = []

        async def call():
            attempts.append(1)
            raise RuntimeError('model returned no choices: {"message": "insufficient credits"}')

        with self.assertRaises(RuntimeError):
            await call_with_retry(call, budget=RetryBudget(), sleep=self._sleep)

        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.slept, [])

    async def test_reports_each_retry_for_the_journal(self):
        seen = []

        async def call():
            raise LIVE_NO_RESPONSE

        with self.assertRaises(RuntimeError):
            await call_with_retry(
                call,
                budget=RetryBudget(max_attempts=2),
                on_retry=lambda attempt, reason, delay, exc: seen.append((attempt, reason, delay)),
                sleep=self._sleep,
            )

        self.assertEqual(seen, [(1, "upstream returned no choices", 2.0)])

    async def test_reports_why_it_gave_up(self):
        seen = []

        async def call():
            raise TruncatedOutput("truncated")

        with self.assertRaises(TruncatedOutput):
            await call_with_retry(
                call,
                budget=RetryBudget(),
                on_giveup=lambda attempt, reason, exc: seen.append((attempt, reason)),
                sleep=self._sleep,
            )

        self.assertEqual(seen, [(1, "not retryable")])


class SummarizeTest(unittest.TestCase):
    def test_names_the_type_and_collapses_newlines(self):
        summary = summarize(ValueError("line one\nline two"))

        self.assertEqual(summary, "ValueError: line one line two")

    def test_truncates(self):
        self.assertLessEqual(len(summarize(ValueError("x" * 500), limit=50)), 50 + len("ValueError: "))


if __name__ == "__main__":
    unittest.main()

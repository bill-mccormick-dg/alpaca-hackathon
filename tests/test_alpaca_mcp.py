import unittest
from unittest.mock import patch

from bot import alpaca_mcp


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, text):
        self.content = [FakeContent(text)]


class FakeSession:
    """Returns each canned result in order; the last one repeats."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def call_tool(self, name, arguments):
        self.calls += 1
        idx = min(self.calls - 1, len(self.results) - 1)
        return self.results[idx]


CONNECT_ERROR = FakeResult(
    "Error calling tool 'get_clock': Request error (ConnectError): "
    "[Errno -3] Temporary failure in name resolution"
)
OK = FakeResult('{"data": {"is_open": true}}')
HTTP_403 = FakeResult("Error calling tool 'get_option_chain': HTTP 403 subscription does not permit")


def _client(results):
    client = alpaca_mcp.AlpacaMCPClient("k", "s")
    client.session = FakeSession(results)
    return client


class CallToolRetryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(alpaca_mcp, "RETRY_DELAY_SEC", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_returns_immediately_on_success(self):
        client = _client([OK])
        result = await client.call_tool("get_clock")
        self.assertIs(result, OK)
        self.assertEqual(client.session.calls, 1)

    async def test_retries_transient_connect_error_then_succeeds(self):
        client = _client([CONNECT_ERROR, OK])
        result = await client.call_tool("get_clock")
        self.assertIs(result, OK)
        self.assertEqual(client.session.calls, 2)

    async def test_gives_up_after_retries_and_returns_last_error(self):
        client = _client([CONNECT_ERROR])
        result = await client.call_tool("get_clock")
        self.assertIs(result, CONNECT_ERROR)
        self.assertEqual(client.session.calls, alpaca_mcp.RETRIES)

    async def test_does_not_retry_non_connect_errors(self):
        # A 403 is a real answer from Alpaca (e.g. no OPRA subscription) -
        # retrying it would just waste time and hide the cause.
        client = _client([HTTP_403, OK])
        result = await client.call_tool("get_option_chain")
        self.assertIs(result, HTTP_403)
        self.assertEqual(client.session.calls, 1)


if __name__ == "__main__":
    unittest.main()

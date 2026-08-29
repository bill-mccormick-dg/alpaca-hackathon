import json
import unittest

import httpx

from bot.featherless import BASE_URL, DEFAULT_MODEL, FeatherlessClient


class ChatTest(unittest.IsolatedAsyncioTestCase):
    async def test_sends_bearer_auth_and_default_model(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 5}},
            )

        client = FeatherlessClient("secret-key", transport=httpx.MockTransport(handler))
        result = await client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(captured["url"], BASE_URL + "/chat/completions")
        self.assertEqual(captured["auth"], "Bearer secret-key")
        self.assertEqual(captured["body"]["model"], DEFAULT_MODEL)
        self.assertEqual(captured["body"]["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(result["choices"][0]["message"]["content"], "hi")

    async def test_extra_kwargs_pass_through_to_request_body(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

        client = FeatherlessClient("secret-key", transport=httpx.MockTransport(handler))
        await client.chat([{"role": "user", "content": "hello"}], max_tokens=20, temperature=0)

        self.assertEqual(captured["body"]["max_tokens"], 20)
        self.assertEqual(captured["body"]["temperature"], 0)

    async def test_raises_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = FeatherlessClient("bad-key", transport=httpx.MockTransport(handler))
        with self.assertRaises(httpx.HTTPStatusError):
            await client.chat([{"role": "user", "content": "hello"}])

    async def test_custom_model_overrides_default(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

        client = FeatherlessClient(
            "secret-key", model="Qwen/Qwen3-8B", transport=httpx.MockTransport(handler)
        )
        await client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(captured["body"]["model"], "Qwen/Qwen3-8B")

    async def test_timeout_is_configurable_and_defaults_to_60(self):
        self.assertEqual(FeatherlessClient("k").timeout, 60)
        self.assertEqual(FeatherlessClient("k", timeout=12.5).timeout, 12.5)


if __name__ == "__main__":
    unittest.main()

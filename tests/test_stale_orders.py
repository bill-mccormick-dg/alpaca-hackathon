"""bot/stale_orders.py - the model's unfilled entry buys from an earlier
cycle are cancelled before the cycle looks at the account (#171)."""

import unittest

from bot import stale_orders


class FakeContent:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, text):
        self.content = [FakeContent(text)]


class ScriptedBroker:
    """cancel_order_by_id answers from a per-id script: a string is the
    result text, an Exception is raised. Records every call."""

    def __init__(self, script=None):
        self.script = script or {}
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        assert name == "cancel_order_by_id", name
        answer = self.script.get(arguments["order_id"], "{}")
        if isinstance(answer, Exception):
            raise answer
        return FakeResult(answer)


def order(id, side="buy", client_order_id="hb-20260901-120017-QQQ260903P00709000-buy", **more):
    return {"id": id, "side": side, "client_order_id": client_order_id, "symbol": "QQQ260903P00709000",
            "qty": 4.0, "limit_price": 3.56, "submitted_at": "2026-09-01T16:00:17Z", **more}


class CancelStaleEntriesTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_bots_own_buys_are_cancelled(self):
        """A resting sell is an exit that still wants to fill; an order
        without the bot's prefix is a human's."""
        broker = ScriptedBroker()
        out = await stale_orders.cancel_stale_entries(broker, [
            order("a"),
            order("b", side="sell", client_order_id="hb-20260901-125009-QQQ260903P00709000-sell"),
            order("c", client_order_id="manual-from-the-dashboard"),
            order("d", client_order_id=None),
        ])
        self.assertEqual([args["order_id"] for _, args in broker.calls], ["a"])
        self.assertEqual([(o["id"], o["ok"], o["detail"]) for o in out], [("a", True, "cancelled")])

    async def test_a_failed_cancel_is_reported_not_raised(self):
        broker = ScriptedBroker({"a": RuntimeError("boom"), "b": '{"error": {"detail": "The order status is not cancelable."}}'})
        out = await stale_orders.cancel_stale_entries(broker, [order("a"), order("b"), order("c")])
        self.assertEqual([(o["id"], o["ok"]) for o in out], [("a", False), ("b", False), ("c", True)])
        self.assertIn("boom", out[0]["detail"])
        self.assertIn("not cancelable", out[1]["detail"])

    async def test_nothing_resting_or_unknown_means_no_calls(self):
        broker = ScriptedBroker()
        self.assertEqual(await stale_orders.cancel_stale_entries(broker, []), [])
        self.assertEqual(await stale_orders.cancel_stale_entries(broker, None), [])
        self.assertEqual(broker.calls, [])

    def test_the_prefix_is_the_one_execute_writes(self):
        from bot.execute import client_order_id
        from bot.models import Proposal

        cid = client_order_id(Proposal("option", "QQQ260903P00709000", "buy", 4))
        self.assertTrue(stale_orders.is_stale_entry({"side": "buy", "client_order_id": cid}))


if __name__ == "__main__":
    unittest.main()

"""Journal -> readable report rendering (issue #87).

The point of these is the Home Assistant contract: a sensor's state is capped
at 255 characters, so anything a teammate actually reads has to travel as a
JSON attribute. Get that wrong and the entity silently goes `unavailable`."""

import unittest

from bot import report

TS = "2026-09-01T10:15:30-04:00"


def _event(event, **fields):
    return {"ts": TS, "event": event, **fields}


FILL = _event(
    "order_submitted",
    side="buy",
    qty=2,
    symbol="NVDA260909C00220000",
    price=3.71,
    reason="Bullish on the high-volatility tech leader",
    order_id="abc123",
)
REJECT = _event("order_rejected", side="buy", qty=99, symbol="SPY", price=1.0, reason="qty exceeds cap")
DRY = _event("dry_run", side="buy", qty=1, symbol="QQQ", price=2.5, reason="rehearsal")


class RecentTradesTest(unittest.TestCase):
    def test_keeps_only_trade_shaped_events(self):
        events = [_event("cycle_start"), FILL, _event("config"), _event("tool_call"), REJECT]

        trades = report.recent_trades(events)

        self.assertEqual([t["event"] for t in trades], ["order_submitted", "order_rejected"])

    def test_keeps_the_most_recent_within_the_limit_newest_last(self):
        events = [dict(FILL, symbol=f"SYM{i}") for i in range(20)]

        trades = report.recent_trades(events, limit=3)

        self.assertEqual([t["symbol"] for t in trades], ["SYM17", "SYM18", "SYM19"])

    def test_extracts_the_fields_a_reader_needs(self):
        t = report.recent_trades([FILL])[0]

        self.assertEqual(t["time"], "10:15")
        self.assertEqual((t["side"], t["qty"], t["symbol"]), ("buy", 2, "NVDA260909C00220000"))
        self.assertIn("Bullish", t["reason"])

    def test_survives_a_malformed_timestamp(self):
        t = report.recent_trades([dict(FILL, ts="not-a-date")])[0]

        self.assertIsInstance(t["time"], str)

    def test_missing_fields_do_not_raise(self):
        trades = report.recent_trades([{"ts": TS, "event": "order_submitted"}])

        self.assertEqual(len(trades), 1)


class RenderTest(unittest.TestCase):
    def test_renders_the_reason_the_model_gave(self):
        md = report.render_trades_markdown(report.recent_trades([FILL]))

        self.assertIn("10:15", md)
        self.assertIn("NVDA260909C00220000", md)
        self.assertIn("Bullish", md)
        self.assertIn("FILLED", md)

    def test_marks_exits(self):
        md = report.render_trades_markdown(report.recent_trades([dict(FILL, exit=True)]))

        self.assertIn("(exit)", md)

    def test_empty_day_says_so_rather_than_rendering_nothing(self):
        md = report.render_trades_markdown([], account="official")

        self.assertIn("No trades yet", md)
        self.assertIn("official", md)

    def test_long_reasons_are_clipped(self):
        md = report.render_trades_markdown(report.recent_trades([dict(FILL, reason="x" * 500)]))

        self.assertLess(len(md), 400)

    def test_newlines_in_a_reason_do_not_break_the_line_format(self):
        md = report.render_trades_markdown(report.recent_trades([dict(FILL, reason="a\nb\nc")]))

        self.assertIn("a b c", md)


class SummaryTest(unittest.TestCase):
    def test_counts_by_outcome(self):
        summary = report.trades_summary(report.recent_trades([FILL, FILL, REJECT, DRY]))

        self.assertIn("2 filled", summary)
        self.assertIn("1 rejected", summary)
        self.assertIn("1 dry-run", summary)

    def test_empty(self):
        self.assertEqual(report.trades_summary([]), "no trades")


class PayloadTest(unittest.TestCase):
    """HA caps sensor state at 255 chars - the whole reason for the split."""

    def test_state_fits_home_assistants_limit(self):
        events = [dict(FILL, reason="y" * 300) for _ in range(50)]

        payload = report.trades_payload(events, account="official")

        self.assertLessEqual(len(payload["state"]), report.MAX_STATE_CHARS)

    def test_markdown_travels_in_attributes(self):
        payload = report.trades_payload([FILL], account="official")

        self.assertIn("NVDA260909C00220000", payload["attributes"]["markdown"])
        self.assertEqual(payload["attributes"]["count"], 1)
        self.assertEqual(payload["attributes"]["account"], "official")

    def test_empty_journal_still_produces_a_usable_payload(self):
        payload = report.trades_payload([], account="test")

        self.assertEqual(payload["state"], "no trades")
        self.assertIn("No trades yet", payload["attributes"]["markdown"])


class EodPayloadTest(unittest.TestCase):
    def test_reuses_the_digest_markdown_verbatim(self):
        md = "# EOD 2026-09-01\n\nEquity 101,000 (+1.0%)\n"

        payload = report.eod_payload(md, day="2026-09-01", account="official")

        self.assertEqual(payload["attributes"]["markdown"], md.strip())
        self.assertIn("2026-09-01", payload["state"])

    def test_state_fits_the_limit_even_for_a_long_digest(self):
        payload = report.eod_payload("z" * 10000, day="2026-09-01")

        self.assertLessEqual(len(payload["state"]), report.MAX_STATE_CHARS)

    def test_missing_digest_is_explicit_rather_than_blank(self):
        payload = report.eod_payload("", day="2026-09-01")

        self.assertEqual(payload["state"], "none")
        self.assertIn("No end-of-day digest", payload["attributes"]["markdown"])


if __name__ == "__main__":
    unittest.main()


class FeedTest(unittest.TestCase):
    """The live journal feed (#134)."""

    def test_every_event_type_renders_a_line(self):
        """Unknown events must fall through to name+fields, never vanish -
        a new journal event should be visible in the feed before anyone
        teaches the renderer about it."""
        for record in (
            _event("cycle_start", equity=100000, day_pnl=0, positions=0),
            _event("predictions", SPY={"reference_close": 7711.76, "p_above_reference": 0.27, "suppressed": None}),
            _event("decision", count=1, model="m", usage={"total_tokens": 6000}),
            _event("order_rejected", side="sell", qty=10, symbol="S", detail="model exit blocked: ..."),
            _event("never_seen_before", anything=1),
        ):
            line = report.render_feed_line(record)
            self.assertIn("10:15", line)
            self.assertTrue(len(line) > 10, record["event"])

    def test_payload_is_fenced_and_state_fits_ha(self):
        events = [dict(FILL, reason="r" * 400)] * 60
        payload = report.feed_payload(events, "official")
        self.assertLessEqual(len(payload["state"]), report.MAX_STATE_CHARS)
        self.assertTrue(payload["attributes"]["markdown"].startswith("```text\n"))
        self.assertEqual(payload["attributes"]["count"], report.FEED_LINES)

    def test_a_withheld_prior_says_so(self):
        line = report.render_feed_line(_event("predictions", QQQ={
            "reference_close": 29433, "p_above_reference": 0.5, "suppressed": "thin: volume 45 < 250"}))
        self.assertIn("withheld", line)
        self.assertIn("thin", line)

    def test_empty_feed_has_an_empty_body_not_a_bare_fence(self):
        payload = report.feed_payload([], "official")
        self.assertEqual(payload["attributes"]["markdown"], "")
        self.assertEqual(payload["state"], "no events yet")

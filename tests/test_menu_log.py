"""The candidate menu goes to its own file, as an observer (#269 follow-up).

bot/decide.py has always computed implied volatility and the Greeks per
candidate contract and put them in the prompt; the `decision` event stored
the model's answer and threw the menu away. That left no way to ask, after
the fact, whether a volatility signal would have helped - the inputs were
gone. These tests pin the three properties that make the record trustworthy
and safe to land during a baseline:

  * it is the menu the model actually saw, not a recomputation,
  * it never reaches journal.jsonl or the MQTT feed,
  * it cannot break a cycle when the write fails.
"""

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bot import decide, journal
from tests.test_decide import FakeFeatherlessClient
from tests.test_decide import _config as _decide_config

TODAY = date(2026, 9, 8)


def _snapshot() -> dict:
    return {
        "account": {"equity": 100000},
        "options": {
            "SPY": {
                "underlying_price": 650.0,
                "contracts": {
                    "SPY260911C00655000": {
                        "latestQuote": {"bp": 3.10, "ap": 3.30},
                        "latestTrade": {"p": 3.20},
                        "impliedVolatility": 0.1834,
                        "greeks": {"delta": 0.42, "gamma": 0.031, "theta": -0.19, "vega": 0.28},
                    },
                },
            },
        },
    }


def _config(**over) -> dict:
    """Reuses test_decide's fixture rather than keeping a second one: the
    prompt-equality test below is only meaningful against the same config
    shape the prompt tests use."""
    cfg = _decide_config(underlyings=["SPY"])
    cfg.update(over)
    return cfg


class MenuFile(unittest.TestCase):
    def test_one_file_per_account_per_day(self):
        a = journal.menu_file("official", "2026-09-08")
        b = journal.menu_file("test", "2026-09-08")
        c = journal.menu_file("official", "2026-09-09")
        self.assertEqual(a.name, "menu-official-2026-09-08.jsonl")
        self.assertEqual(b.name, "menu-test-2026-09-08.jsonl")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_no_account_is_official(self):
        self.assertEqual(
            journal.menu_file(None, "2026-09-08").name, "menu-official-2026-09-08.jsonl"
        )

    def test_round_trips_through_read_menus(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "menu.jsonl"
            menu = {"SPY": {"underlying_price": 650.0, "contracts": [{"symbol": "X", "iv": 0.18}]}}
            journal.log_menu("official", menu, path=path, model="m", dry_run=False)
            journal.log_menu("official", menu, path=path, model="m", dry_run=False)
            rows = journal.read_menus("official", path=path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["underlyings"], menu)
        self.assertEqual(rows[0]["account"], "official")
        self.assertEqual(rows[0]["model"], "m")
        self.assertIn("ts", rows[0])

    def test_empty_menu_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "menu.jsonl"
            self.assertIsNone(journal.log_menu("official", {}, path=path))
            self.assertFalse(path.exists())

    def test_a_failed_write_is_swallowed(self):
        """An observer must never cost a cycle."""
        unwritable = Path("/proc/definitely/not/writable/menu.jsonl")
        self.assertIsNone(journal.log_menu("official", {"SPY": {}}, path=unwritable))

    def test_malformed_lines_are_skipped(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "menu.jsonl"
            path.write_text('{"ts": "1", "underlyings": {}}\n{half written\n\n')
            self.assertEqual(len(journal.read_menus("official", path=path)), 1)


class MenuIsWhatThePromptSaw(unittest.TestCase):
    def test_decision_menu_matches_the_prompt_payload(self):
        """The record has to be the menu the model saw. Recomputing it later
        would look identical today and diverge the first time selection or
        the Greeks change."""
        snap, cfg = _snapshot(), _config()
        menu = decide._summarize_options(snap, cfg, TODAY)
        prompt = decide.build_prompt(snap, cfg, TODAY, options=menu)
        # Every contract in the journaled menu appears in the prompt text.
        for block in menu.values():
            for contract in block["contracts"]:
                self.assertIn(contract["symbol"], prompt)

    def test_passing_options_does_not_change_the_prompt(self):
        """Threading the menu through must be a pure refactor - a prompt that
        differs by one character is a strategy change, not a measurement."""
        snap, cfg = _snapshot(), _config()
        default = decide.build_prompt(snap, cfg, TODAY)
        threaded = decide.build_prompt(snap, cfg, TODAY, options=decide._summarize_options(snap, cfg, TODAY))
        self.assertEqual(default, threaded)

    def test_menu_carries_the_iv_that_motivated_this(self):
        menu = decide._summarize_options(_snapshot(), _config(), TODAY)
        contracts = menu["SPY"]["contracts"]
        self.assertTrue(contracts, "fixture produced no menu")
        self.assertEqual(contracts[0]["iv"], 0.1834)
        self.assertEqual(contracts[0]["greeks_source"], "alpaca")


class DecideCarriesTheMenuOut(unittest.IsolatedAsyncioTestCase):
    """The unit tests above exercise the pieces; this one walks the real path,
    because Decision.menu being populated is the whole point and a wiring
    mistake there would leave every file empty while every other test passed."""

    async def test_decision_menu_is_populated_and_matches_the_prompt(self):
        client = FakeFeatherlessClient("[]")
        decision = await decide.decide(_snapshot(), _config(), client, today=TODAY)
        self.assertTrue(decision.menu, "decide() returned an empty menu")
        self.assertEqual(decision.menu, decide._summarize_options(_snapshot(), _config(), TODAY))
        prompt = client.calls[0][0][0]["content"]
        for block in decision.menu.values():
            for contract in block["contracts"]:
                self.assertIn(contract["symbol"], prompt)

    async def test_the_journaled_record_is_that_menu(self):
        client = FakeFeatherlessClient("[]")
        decision = await decide.decide(_snapshot(), _config(), client, today=TODAY)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "menu.jsonl"
            journal.log_menu("official", decision.menu, path=path, model=decision.model, dry_run=False)
            rows = journal.read_menus("official", path=path)
        self.assertEqual(rows[0]["underlyings"], decision.menu)
        self.assertEqual(rows[0]["dry_run"], False)


class StaysOutOfTheHotPaths(unittest.TestCase):
    def test_log_menu_does_not_publish_to_mqtt(self):
        """journal.log() republishes everything to the MQTT feed ahead of its
        allow-list (bot/mqtt.py::on_event -> _publish_feed), so a menu routed
        through it would push a 60-contract blob at Home Assistant every
        cycle. log_menu must not go near it."""
        with TemporaryDirectory() as tmp, patch("bot.mqtt.on_event") as on_event:
            journal.log_menu("official", {"SPY": {}}, path=Path(tmp) / "m.jsonl")
            on_event.assert_not_called()

    def test_log_menu_does_not_touch_the_journal(self):
        """read_events("all") parses the whole journal every cycle (learning,
        holdings). The menu stream must not land in it."""
        with TemporaryDirectory() as tmp:
            j = Path(tmp) / "journal.jsonl"
            menu_path = Path(tmp) / "menu.jsonl"
            journal.log("cycle_start", journal=j, account="official")
            journal.log_menu("official", {"SPY": {"contracts": [{"symbol": "X"}]}}, path=menu_path)
            events = [json.loads(line) for line in j.read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["cycle_start"])
        self.assertNotIn("underlyings", events[0])


if __name__ == "__main__":
    unittest.main()

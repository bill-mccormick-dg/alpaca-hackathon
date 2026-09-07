"""`parked_accounts`: an account that may not open new positions.

Added 2026-09-07 to stop the judged three trading during judging week without
touching the baseline accounts that share their config files. The gate itself
is three lines in run_cycle.py; what needs pinning is the two properties that
make it safe, and the one that makes it necessary.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ("config.yaml", "config-test.yaml", "config-variants/mixed.yaml")
PARKED = {"official", "test", "mixed"}
BASELINE = {"base_a", "base_b", "base_mixed"}


def _config(name):
    return yaml.safe_load((ROOT / name).read_text())


class ParkedAccountsConfig(unittest.TestCase):
    def test_every_config_carries_the_same_park_list(self):
        """An account is parked by NAME, and it reads the list out of whatever
        config it happens to load. A list present in one file and missing from
        another parks an account on one config and not another - which is the
        silent-default failure the fail-closed manifest exists to prevent."""
        lists = {name: _config(name).get("parked_accounts") for name in CONFIGS}
        for name, value in lists.items():
            self.assertIsNotNone(value, f"{name} has no parked_accounts list")
        first = lists[CONFIGS[0]]
        for name, value in lists.items():
            self.assertEqual(value, first, f"{name}'s park list differs from {CONFIGS[0]}'s")

    def test_the_judged_three_are_parked(self):
        self.assertEqual(set(_config("config.yaml")["parked_accounts"]), PARKED)

    def test_no_baseline_account_is_parked(self):
        """base_a and base_b load config.yaml, the same file `official` loads.
        The whole reason this gate is account-scoped rather than config-scoped
        is that parking official must not park them."""
        for name in CONFIGS:
            parked = set(_config(name)["parked_accounts"])
            self.assertEqual(parked & BASELINE, set(), f"{name} parks a baseline account")

    def test_park_is_journaled(self):
        """A flat day on a parked account must be attributable to the park and
        not misread as the strategy declining to trade."""
        from bot.config import TRACKED_KEYS
        self.assertIn("parked_accounts", TRACKED_KEYS)


class GatePlacement(unittest.TestCase):
    """The gate's position in run_cycle.py is load-bearing, so assert it
    rather than trusting a comment."""

    def setUp(self):
        self.src = (ROOT / "run_cycle.py").read_text()

    def test_park_gate_runs_after_exits(self):
        """Parking must not skip exits. A parked account still holds positions,
        and expiry_close_dte is what keeps a contract from running into
        expiration and being assigned - going fully inert would be a bigger
        change to the account's equity than trading would."""
        exits = self.src.index("exit_proposals = check_exits(")
        park = self.src.index('config.get("parked_accounts")')
        self.assertLess(exits, park, "the park gate moved above the exit block")

    def test_park_gate_runs_before_the_model_is_called(self):
        park = self.src.index('config.get("parked_accounts")')
        decide = self.src.index("decision = await call_with_retry(")
        self.assertLess(park, decide, "a parked account would still pay for a model call")

    def test_park_gate_is_keyed_on_the_account_not_the_config(self):
        gate = self.src[self.src.index('config.get("parked_accounts")') - 200:][:260]
        self.assertIn("args.account in", gate)


if __name__ == "__main__":
    unittest.main()

"""The one guard that keys on the credentials rather than on the --account
string. See bot/identity.py for why the policy is deliberately asymmetric."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bot.identity import (
    OFFICIAL_ACCOUNT_NUMBER,
    check_account_identity,
    identity_mismatch,
)
from bot.models import AccountState, Proposal
from bot.risk import RiskManager
from tests.test_risk import make_config

OTHER_ACCOUNT = "PA9TEST00001"


class IdentityMismatchTest(unittest.TestCase):
    """identity_mismatch only speaks when the number is KNOWN and wrong."""

    def test_official_name_with_official_number_is_fine(self):
        self.assertEqual(identity_mismatch("official", OFFICIAL_ACCOUNT_NUMBER), "")

    def test_other_name_with_other_number_is_fine(self):
        self.assertEqual(identity_mismatch("test", OTHER_ACCOUNT), "")

    def test_non_official_name_resolving_to_the_judging_account_is_a_mismatch(self):
        """The catastrophic case this whole module exists for."""
        reason = identity_mismatch("test", OFFICIAL_ACCOUNT_NUMBER)

        self.assertIn("JUDGING", reason)
        self.assertIn(OFFICIAL_ACCOUNT_NUMBER, reason)

    def test_official_name_resolving_elsewhere_is_a_mismatch(self):
        reason = identity_mismatch("official", OTHER_ACCOUNT)

        self.assertIn(OTHER_ACCOUNT, reason)
        self.assertIn(OFFICIAL_ACCOUNT_NUMBER, reason)

    def test_unknown_number_is_never_a_mismatch(self):
        for name in ("official", "test", "kimi26"):
            for number in (None, "", "   "):
                self.assertEqual(identity_mismatch(name, number), "")

    def test_name_is_normalized(self):
        self.assertNotEqual(identity_mismatch("  OFFICIAL  ", OTHER_ACCOUNT), "")
        self.assertEqual(identity_mismatch("  OFFICIAL  ", OFFICIAL_ACCOUNT_NUMBER), "")

    def test_number_is_normalized(self):
        self.assertEqual(identity_mismatch("official", f"  {OFFICIAL_ACCOUNT_NUMBER} "), "")


class CheckAccountIdentityTest(unittest.TestCase):
    def test_confirmed_identity_is_allowed_and_silent(self):
        self.assertEqual(check_account_identity("official", OFFICIAL_ACCOUNT_NUMBER), (True, ""))
        self.assertEqual(check_account_identity("test", OTHER_ACCOUNT), (True, ""))

    def test_mismatch_refuses(self):
        allowed, message = check_account_identity("test", OFFICIAL_ACCOUNT_NUMBER)

        self.assertFalse(allowed)
        self.assertIn("refusing", message)

    def test_official_mismatch_refuses(self):
        allowed, _ = check_account_identity("official", OTHER_ACCOUNT)

        self.assertFalse(allowed)

    def test_unverifiable_number_fails_closed_for_a_challenger(self):
        """Refusing a challenger cycle costs nothing; trading the judging
        account by accident costs the competition."""
        for number in (None, ""):
            allowed, message = check_account_identity("test", number)

            self.assertFalse(allowed)
            self.assertIn("could not verify", message)

    def test_unverifiable_number_warns_but_proceeds_for_the_official_account(self):
        """A parsing regression must not take the judged account out of the
        market on the morning the competition starts."""
        allowed, message = check_account_identity("official", None)

        self.assertTrue(allowed)
        self.assertIn("WARNING", message)
        self.assertIn(OFFICIAL_ACCOUNT_NUMBER, message)

    def test_missing_account_name_fails_closed(self):
        allowed, _ = check_account_identity(None, None)

        self.assertFalse(allowed)


class CheckOrderIdentityTest(unittest.TestCase):
    """The funnel every order passes through refuses a known mismatch, and stays
    inert when there is no account number to check (so the several hundred other
    risk tests need not synthesize one)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.mid_session = datetime(2026, 1, 15, 12, 0)
        self.proposal = Proposal(instrument="stock", symbol="AAPL", side="buy", qty=1)

    def _risk(self, account):
        return RiskManager(make_config(), logs_dir=Path(self.tmpdir.name), account=account)

    def _account(self, account_number):
        return AccountState(
            equity=100000, start_of_day_equity=100000, cash=100000, account_number=account_number
        )

    def test_refuses_an_order_when_a_challenger_is_on_the_judging_account(self):
        ok, reason = self._risk("test").check_order(
            self.proposal, self._account(OFFICIAL_ACCOUNT_NUMBER), 100.0, self.mid_session
        )

        self.assertFalse(ok)
        self.assertIn("JUDGING", reason)

    def test_refuses_an_order_when_official_is_pointed_elsewhere(self):
        ok, reason = self._risk("official").check_order(
            self.proposal, self._account(OTHER_ACCOUNT), 100.0, self.mid_session
        )

        self.assertFalse(ok)
        self.assertIn("mismatch", reason)

    def test_allows_a_matching_account(self):
        ok, _ = self._risk("official").check_order(
            self.proposal, self._account(OFFICIAL_ACCOUNT_NUMBER), 100.0, self.mid_session
        )

        self.assertTrue(ok)

    def test_stays_inert_without_an_account_number(self):
        ok, _ = self._risk("test").check_order(
            self.proposal, self._account(None), 100.0, self.mid_session
        )

        self.assertTrue(ok)

    def test_official_account_name_survives_the_halt_file_scoping(self):
        """RiskManager.account is None for `official` so its halt files stay
        unsuffixed; the identity check needs the real name regardless."""
        risk = self._risk("official")

        self.assertIsNone(risk.account)
        self.assertEqual(risk.account_name, "official")

    def test_default_account_name_is_test(self):
        self.assertEqual(RiskManager(make_config(), logs_dir=Path(self.tmpdir.name)).account_name, "test")


if __name__ == "__main__":
    unittest.main()

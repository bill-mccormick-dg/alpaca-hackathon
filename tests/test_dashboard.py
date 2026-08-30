"""The team dashboard must stay read-only (issue #87).

Home Assistant has no per-entity permissions for non-admin users: anything
rendered on a dashboard they can open, they can operate. The separation
between the operational dashboard (kill switches) and the team dashboard
(sensors only) is therefore the entire access-control story, and it lives in
a Jinja template where nothing else would catch a regression.

Deliberately asserts against the raw template text rather than rendering it -
Jinja2 is an Ansible dependency, not one of this project's four, and the whole
suite stays runnable with `python -m unittest` and no extra install."""

import re
import unittest
from pathlib import Path

from bot import mqtt

TEMPLATES = Path(__file__).resolve().parent.parent / "ansible/roles/ha-dashboard/templates"
TEAM = TEMPLATES / "dashboard_team.yaml.j2"
OPERATIONAL = TEMPLATES / "dashboard.yaml.j2"

# Entity domains that can change the world from a dashboard card.
CONTROL_DOMAINS = ("switch", "button", "input_boolean", "input_number", "script", "automation", "climate")


def _uncommented(path: Path) -> str:
    """Template text with comment lines stripped, so a warning comment that
    names `switch.` doesn't read as a control."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))


class TeamDashboardIsReadOnlyTest(unittest.TestCase):
    def test_template_exists(self):
        self.assertTrue(TEAM.is_file(), f"{TEAM} is missing")

    def test_no_control_entities_anywhere(self):
        body = _uncommented(TEAM)

        for domain in CONTROL_DOMAINS:
            found = re.findall(rf"\b{domain}\.[a-z0-9_]+", body)
            self.assertEqual(found, [], f"team dashboard must not expose {domain} entities: {found}")

    def test_no_service_calls(self):
        body = _uncommented(TEAM)

        for forbidden in ("tap_action", "hold_action", "service:", "perform_action"):
            self.assertNotIn(forbidden, body, f"team dashboard must not carry {forbidden!r}")

    def test_shows_the_report_sensors_teammates_come_for(self):
        body = _uncommented(TEAM)

        for suffix in mqtt.ATTRIBUTE_SENSORS:
            self.assertIn(suffix, body, f"team dashboard should render the {suffix} sensor")

    def test_entity_references_match_the_published_naming_scheme(self):
        """A renamed sensor orphans a card silently; EntityIdDerivationTest
        pins the publisher side, this pins the consumer side."""
        body = _uncommented(TEAM)
        referenced = set(re.findall(r"sensor\.\{\{ ha_entity_prefix \}\}_\{\{ acct\.id \}\}_([a-z_]+)", body))
        known = set(mqtt.STATE_SENSORS) | set(mqtt.ATTRIBUTE_SENSORS)

        self.assertTrue(referenced, "no templated sensor references found - did the template change shape?")
        self.assertEqual(referenced - known, set(), "dashboard references sensors nothing publishes")


class OperationalDashboardStillHasTheControlsTest(unittest.TestCase):
    """The counterpart: the separation is only meaningful if the kill switch
    really does live on the other dashboard."""

    def test_kill_switch_is_on_the_operational_dashboard(self):
        body = _uncommented(OPERATIONAL)

        self.assertIn("kill_switch", body)
        self.assertRegex(body, r"switch\.")


class PushNotificationTemplateTest(unittest.TestCase):
    """Push automations exist only when a real notify service is configured -
    persistent_notification is in-app only and never reaches a phone (#86)."""

    def setUp(self):
        self.body = _uncommented(TEMPLATES / "automations_block.yaml.j2")

    def test_push_actions_are_guarded_by_a_configured_notify_service(self):
        self.assertIn("{% if ha_notify_service %}", self.body)

    def test_the_alerts_that_matter_all_have_a_push_automation(self):
        for event in ("manual_halt", "daily_loss_halt", "identity_refused", "identity_unverified"):
            self.assertIn(event, self.body, f"no automation references {event}")

    def test_identity_alerts_are_not_filtered_to_one_account(self):
        """A challenger resolving to the judged account is the whole point of
        the guard - filtering it to `official` would hide the case."""
        self.assertIn("/+/event/identity_refused", self.body)

    def test_stall_detection_reads_a_sensor_that_is_actually_published(self):
        self.assertIn("_equity", self.body)
        self.assertIn("equity", mqtt.STATE_SENSORS)

    def test_routine_trade_events_do_not_push(self):
        """order_submitted/rejected stay in-app; a muted channel loses the
        halt alert with it."""
        push_section = self.body.split("{% if ha_notify_service %}", 1)
        self.assertEqual(len(push_section), 2, "push section marker missing")
        for event in ("order_rejected", "dry_run"):
            self.assertNotIn(event, push_section[1])


if __name__ == "__main__":
    unittest.main()

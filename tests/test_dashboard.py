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


if __name__ == "__main__":
    unittest.main()

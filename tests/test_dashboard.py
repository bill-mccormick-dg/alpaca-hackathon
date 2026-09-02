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
import sys
import unittest
from pathlib import Path

from bot import mqtt

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ansible/roles/ha-dashboard/templates"
TEAM = TEMPLATES / "dashboard_team.yaml.j2"
OPERATIONAL = TEMPLATES / "dashboard.yaml.j2"

# Entity domains that can change the world from a dashboard card.
# `select`, `number` and `text` are the domains THIS bridge publishes its
# knobs under (mqtt_bridge.discovery_payloads); leaving them out let the
# team dashboard expose a writable control while this test still passed.
CONTROL_DOMAINS = (
    "switch", "button", "select", "number", "text",
    "input_boolean", "input_number", "input_select", "input_text",
    "script", "automation", "climate",
)


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


class TrimBlocksSafetyTest(unittest.TestCase):
    """Ansible renders templates with trim_blocks=True, which swallows the
    newline immediately after a `%}` tag.

    A line of YAML that ENDS with a Jinja tag therefore gets glued to the next
    line. That silently folded `data:` into a notification message and left one
    automation with no actions at all - valid YAML, wrong structure, no error
    anywhere. It slipped through because it was validated with a plain Jinja
    environment, which does not trim.

    Standalone control-flow lines ({% if %} / {% endif %} alone on a line) are
    exempt: swallowing their newline is the entire point of trim_blocks."""

    TEMPLATES_TO_CHECK = ("automations_block.yaml.j2", "dashboard.yaml.j2", "dashboard_team.yaml.j2")

    def test_no_value_line_ends_with_a_jinja_tag(self):
        for name in self.TEMPLATES_TO_CHECK:
            path = TEMPLATES / name
            if not path.is_file():
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if not stripped.endswith("%}"):
                    continue
                standalone = stripped.startswith("{%") and stripped.count("{%") == 1
                self.assertTrue(
                    standalone,
                    f"{name}:{n} ends with a Jinja tag; trim_blocks will glue the next "
                    f"line onto it. Put the value on one quoted line instead:\n  {stripped}",
                )


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


class DiagramsAreInSyncTest(unittest.TestCase):
    """The diagrams are generated from scripts/render_diagrams.py, and land in
    three places: docs/diagrams/*.svg, the README, and inline in the deck.

    Inline in the deck because it exports to PDF as a single self-contained
    file - an external reference that fails to load exports as a blank slide,
    and only in the artifact that goes to judges. The cost of pre-generating is
    that a destination can fall behind the generator, so each records a hash of
    the SVG it was written from and this compares them. Cheap enough to
    re-generate here rather than hash-compare, now that there is no Chrome and
    no CDN in the path."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import render_diagrams

        self.rd = render_diagrams
        self.built = render_diagrams.diagrams()

    def test_every_diagram_the_docs_expect_is_generated(self):
        self.assertEqual(set(self.built), {"runtime", "journal", "infra"})

    def test_the_svg_files_on_disk_are_current(self):
        for name, svg in self.built.items():
            out = self.rd.SVG_DIR / f"{name}.svg"
            self.assertTrue(out.exists(), f"docs/diagrams/{name}.svg is missing")
            self.assertEqual(out.read_text(), svg,
                             f"{name}.svg is stale - run: python scripts/render_diagrams.py")

    def test_deck_svg_matches_what_it_was_generated_from(self):
        recorded = self.rd.deck_hashes(self.rd.DECK.read_text())

        for name, svg in self.built.items():
            self.assertIn(name, recorded, f"deck has no {name} diagram")
            self.assertEqual(
                recorded[name], self.rd.source_hash(svg),
                f"{name} diagram is stale - run: python scripts/render_diagrams.py",
            )

    def test_deck_carries_real_svg_not_an_empty_placeholder(self):
        deck = self.rd.DECK.read_text()

        for name in self.built:
            block = deck.split(f"<!-- diagram:{name} -->", 1)[1].split(f"<!-- /diagram:{name} -->", 1)[0]
            self.assertIn("<svg", block, f"{name}: no SVG in the deck")
            self.assertGreater(len(block), 2000, f"{name}: SVG suspiciously small")

    def test_the_deck_lets_its_own_stylesheet_size_the_diagrams(self):
        """An inline max-height would beat the print stylesheet and leave
        scripts/fit_slides.py unable to shrink a diagram slide at all."""
        deck = self.rd.DECK.read_text()

        for name in self.built:
            block = deck.split(f"<!-- diagram:{name} -->", 1)[1].split(f"<!-- /diagram:{name} -->", 1)[0]
            svg = block[block.index("<svg"):block.index(">", block.index("<svg")) + 1]
            self.assertIn("width:100%", svg, f"{name}: deck copy should flex to the slide")
            self.assertNotIn("max-height", svg, f"{name}: inline max-height blocks the fitter")

    def test_readme_shows_the_generated_runtime_diagram(self):
        readme = self.rd.README.read_text()
        block = readme.split(f"<!-- diagram:{self.rd.README_DIAGRAM} -->", 1)[1]
        block = block.split(f"<!-- /diagram:{self.rd.README_DIAGRAM} -->", 1)[0]

        self.assertIn(f"docs/diagrams/{self.rd.README_DIAGRAM}.svg", block)
        self.assertIn("alt=", block, "the README diagram needs alt text")
        self.assertEqual(
            self.rd.deck_hashes(readme).get(self.rd.README_DIAGRAM),
            self.rd.source_hash(self.built[self.rd.README_DIAGRAM]),
            "README diagram is stale - run: python scripts/render_diagrams.py",
        )

    def test_the_docs_reference_every_diagram_they_generate(self):
        """A generated SVG nobody shows is dead weight; a reference to one that
        is not generated is a broken image on the docs site."""
        architecture = (Path(__file__).resolve().parent.parent / "docs/architecture.md").read_text()

        for name in self.built:
            self.assertIn(f'src="diagrams/{name}.svg"', architecture,
                          f"docs/architecture.md never shows the {name} diagram")


class DeckImagesResolveTest(unittest.TestCase):
    """Every image the deck references must exist on disk.

    A broken `src` renders as a broken-image icon on screen and as an empty box
    in the exported PDF - visible only in the artifact that goes to judges,
    which is the failure mode this whole export path keeps producing."""

    # From the repo root, not by walking four levels up out of the ansible
    # role's template directory - the deck has nothing to do with that role,
    # and moving the role would have silently pointed this at the wrong path.
    DECK = ROOT / "submission/video/slides.html"

    def test_every_referenced_image_exists(self):
        deck = self.DECK.read_text()
        srcs = re.findall(r'<img[^>]+src="([^"]+)"', deck)

        self.assertTrue(srcs, "no images in the deck - did the references change shape?")
        for src in srcs:
            path = (self.DECK.parent / src).resolve()
            self.assertTrue(path.is_file(), f"deck references a missing image: {src}")

    def test_images_carry_alt_text(self):
        for tag in re.findall(r"<img[^>]*>", self.DECK.read_text()):
            self.assertIn("alt=", tag, f"image without alt text: {tag[:60]}")


if __name__ == "__main__":
    unittest.main()

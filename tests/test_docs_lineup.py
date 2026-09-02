"""The documented model lineup must match the configs that set it.

#215 documented a lineup, five config PRs landed while it was open, and it
merged green while naming three models that no longer traded anything - the
docs touch no config file, so nothing could have flagged it. Same idea as the
deck's image-reference and diagram-hash tests: a claim about another file is
worth only as much as the check that it still holds.
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STRATEGY = ROOT / "docs/strategy.md"

# The seats the lineup table documents, and the config that decides each.
SEATS = {
    "official": "config.yaml",
    "test": "config-test.yaml",
    "mixed": "config-variants/mixed.yaml",
}


def _config(name):
    with open(ROOT / name) as f:
        return yaml.safe_load(f)


def _table_rows(heading):
    """The rows of the first markdown table under `heading`."""
    body = STRATEGY.read_text().split(heading, 1)[1]
    rows = []
    for line in body.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and not set("".join(cells)) <= set("-: "):
                rows.append(cells)
        elif rows:
            break
    return rows[1:]  # drop the header


class LineupMatchesConfigsTest(unittest.TestCase):
    def test_every_documented_trading_model_is_the_config_s_model(self):
        rows = _table_rows("### The lineup, and the evidence for it")
        documented = {}
        for cells in rows:
            m = re.match(r"`(\w+)` trades", cells[0])
            if m:
                documented[m.group(1)] = cells[1].strip("`")

        self.assertEqual(set(documented), set(SEATS), "the lineup table lost or gained a seat")
        for account, path in SEATS.items():
            self.assertEqual(
                documented[account], _config(path)["model"],
                f"docs/strategy.md says {account} trades on {documented[account]}, "
                f"but {path} says {_config(path)['model']}",
            )

    def test_reviewer_table_matches_what_resolve_review_model_would_pick(self):
        from bot.config import resolve_review_model

        rows = _table_rows("### The critique comes from a model that did not trade")
        documented = {c[0].strip("`"): (c[1], c[2]) for c in rows if c[0].strip("`") in SEATS}

        self.assertEqual(set(documented), set(SEATS), "the reviewer table lost or gained an account")
        for account, path in SEATS.items():
            config = _config(path)
            traded, reviewed = documented[account]
            self.assertIn(config["model"].split("/")[-1], traded,
                          f"reviewer table's 'trades on' for {account} is not {path}'s model")
            self.assertIn(resolve_review_model(config).split("/")[-1], reviewed,
                          f"reviewer table's 'reviewed by' for {account} is not what "
                          "resolve_review_model() returns for it")

    def test_no_account_reviews_its_own_homework(self):
        """The property the reviewer table exists to assert (#218). A pinned
        `review_model` bypasses resolve_review_model's own guard, so this is
        checked against the configs rather than trusted to that function."""
        from bot.config import resolve_review_model

        for account, path in SEATS.items():
            config = _config(path)
            self.assertNotEqual(
                resolve_review_model(config), config["model"],
                f"{account} ({path}) would grade its own homework",
            )


if __name__ == "__main__":
    unittest.main()

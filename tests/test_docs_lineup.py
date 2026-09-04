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
            # None means no independent reviewer was available; eod_review.py
            # then falls back to the trading model. Compare against the
            # EFFECTIVE reviewer, which is what the table documents.
            effective = resolve_review_model(config) or config["model"]
            self.assertIn(effective.split("/")[-1], reviewed,
                          f"reviewer table's 'reviewed by' for {account} is not the "
                          "effective reviewer for it")

    def test_a_self_review_is_never_silent(self):
        """Independent review was given up on 2026-09-08, when all three
        accounts moved onto one model and `review_model_preference` was
        emptied. #218's property - no account grades its own homework - no
        longer holds, and this test is what replaced it: a self-review is
        allowed, but it must be *visible*.

        Two things must stay true. review_choice() must return None rather
        than silently handing back the trading model, so the caller has to
        opt into the fallback; and a config that still has an independent
        reviewer available must still use it."""
        from bot.config import resolve_review_model, review_choice

        for account, path in SEATS.items():
            config = _config(path)
            reviewer, refused = review_choice(config, traded=(config["model"],))
            self.assertIsNone(
                refused,
                f"{account} ({path}) pins a review_model that would be refused "
                "(#218) - drop the pin, or point it at a model nothing trades",
            )
            if reviewer is not None:
                self.assertNotEqual(
                    reviewer, config["model"],
                    f"{account} ({path}) resolved its own model as an "
                    "independent reviewer",
                )

    def test_self_review_is_disclosed_in_the_digest(self):
        """eod_review.py stamps `review_note` when the reviewer traded that
        day. With every account on one model that is now the normal path, so
        the disclosure is the only thing standing between a self-assessment
        and a reader who thinks it was independent."""
        source = (ROOT / "eod_review.py").read_text()
        self.assertIn("review_note", source,
                      "eod_review.py no longer stamps review_note - a "
                      "self-review would now read as an independent one")
        self.assertTrue(
            'if digest["review_model"] in traded' in source,
            "the review_note condition changed; self-review may go undisclosed",
        )


if __name__ == "__main__":
    unittest.main()

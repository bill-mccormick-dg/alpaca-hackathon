"""scripts/fingerprint_run.py - the pre-registration bundle (backlog item 24).

The warnings are the part with judgement in it, and the part that is silent
when it is wrong: a bundle that reports "no warnings" on a run whose
replicate pair was already broken is worse than no bundle, because it is
evidence of a property that does not hold. So each warning gets a test that
proves it fires, and the clean case gets one that proves they all stay quiet.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fingerprint_run", REPO / "scripts" / "fingerprint_run.py")
fingerprint_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fingerprint_run)


def _fp(official=None, test=None, mixed=None, dirty=False) -> dict:
    """A collect() result, with only the fields warnings() reads."""
    base = {"model": "m", "take_profit_pct": 60, "strategy_notes_sha256": "abc123"}
    return {
        "created_utc": "2026-09-07T20:00:00+00:00",
        "git": {"sha": "0" * 40, "subject": "x", "dirty": dirty},
        "files": {},
        "effective_config": {
            "official": {**base, **(official or {})},
            "test": {**base, **(test or {})},
            "mixed": {**base, **(mixed or {})},
        },
    }


def _names(fp) -> list[str]:
    return [w["warning"] for w in fingerprint_run.warnings(fp)]


class Warnings(unittest.TestCase):
    def test_the_clean_case_is_silent(self):
        self.assertEqual(fingerprint_run.warnings(_fp()), [])

    def test_dirty_tree(self):
        self.assertIn("uncommitted_changes", _names(_fp(dirty=True)))

    def test_a_broken_replicate_pair_is_caught(self):
        """The run's whole value is that official and test start identical."""
        w = fingerprint_run.warnings(_fp(test={"take_profit_pct": 55}))
        self.assertEqual([x["warning"] for x in w], ["replicate_pair_broken"])
        self.assertIn("take_profit_pct", w[0]["detail"])

    def test_a_key_present_on_only_one_side_breaks_the_pair(self):
        """The kimi26 failure in miniature: an absent key is not a neutral
        key, it is whatever bot/risk.py's config.get() default happens to be."""
        fp = _fp()
        del fp["effective_config"]["test"]["take_profit_pct"]
        self.assertIn("replicate_pair_broken", _names(fp))

    def test_mixed_may_differ_in_prose_only(self):
        prose_only = _fp(mixed={"strategy_notes_sha256": "different", "instrument_note_sha256": "n"})
        self.assertEqual(fingerprint_run.warnings(prose_only), [])

    def test_mixed_differing_in_a_real_value_is_caught(self):
        w = fingerprint_run.warnings(_fp(mixed={"take_profit_pct": 70}))
        self.assertEqual([x["warning"] for x in w], ["mixed_is_not_prose_only"])
        self.assertIn("take_profit_pct", w[0]["detail"])

    def test_a_past_final_flatten_date_is_caught(self):
        """run_cycle.py refuses every new entry on or after this date, so an
        account carrying an old one is parked and will look like a strategy
        that stopped trading."""
        past = {"final_flatten_date": "2020-01-01"}
        w = fingerprint_run.warnings(_fp(official=past, test=past, mixed=past))
        self.assertIn("final_flatten_date_in_the_past", [x["warning"] for x in w])

    def test_a_future_final_flatten_date_is_fine(self):
        future = {"final_flatten_date": "2099-01-01"}
        self.assertEqual(fingerprint_run.warnings(_fp(official=future, test=future, mixed=future)), [])

    def test_an_empty_final_flatten_date_is_fine(self):
        """The live configs cleared this key after the competition; None must
        not read as 'a date in the past'."""
        blank = {"final_flatten_date": None}
        self.assertEqual(fingerprint_run.warnings(_fp(official=blank, test=blank, mixed=blank)), [])


class EffectiveConfig(unittest.TestCase):
    def test_prose_is_hashed_not_copied(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.yaml"
            p.write_text("model: m\nstrategy_notes: |\n  a long thesis\n")
            eff = fingerprint_run._effective(p)
        self.assertNotIn("strategy_notes", eff)
        self.assertIn("strategy_notes_sha256", eff)
        self.assertEqual(eff["model"], "m")

    def test_a_prose_edit_changes_its_hash(self):
        with TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.yaml", Path(tmp) / "b.yaml"
            a.write_text("strategy_notes: one\n")
            b.write_text("strategy_notes: two\n")
            self.assertNotEqual(
                fingerprint_run._effective(a)["strategy_notes_sha256"],
                fingerprint_run._effective(b)["strategy_notes_sha256"],
            )


class CreateAndCheck(unittest.TestCase):
    def test_bundle_has_all_four_files_and_the_commitment(self):
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            out = fingerprint_run.create("r", "the question", "the hypothesis", "official,test")
            names = sorted(p.name for p in out.iterdir())
            spec = json.loads((out / "strategy_spec.json").read_text())
        self.assertEqual(names, ["data_fingerprint.json", "notes.md", "strategy_spec.json", "warnings.json"])
        self.assertEqual(spec["commitment"], "rules fixed before results")
        self.assertEqual(spec["question"], "the question")
        self.assertEqual(spec["accounts"], ["official", "test"])

    def test_check_is_clean_immediately_after_create(self):
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            fingerprint_run.create("r", "q", "h", "official")
            self.assertEqual(fingerprint_run.check("r"), 0)

    def test_check_fails_when_a_fingerprinted_file_changed(self):
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            fingerprint_run.create("r", "q", "h", "official")
            stored = json.loads((Path(tmp) / "r" / "data_fingerprint.json").read_text())
            stored["files"]["config.yaml"]["sha256"] = "0" * 64
            (Path(tmp) / "r" / "data_fingerprint.json").write_text(json.dumps(stored))
            self.assertEqual(fingerprint_run.check("r"), 1)

    def test_check_on_a_missing_bundle_is_distinct_from_drift(self):
        """2, not 1: "you never registered this" and "it drifted" are
        different answers and a caller may want to treat them differently."""
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            self.assertEqual(fingerprint_run.check("never-created"), 2)


if __name__ == "__main__":
    unittest.main()

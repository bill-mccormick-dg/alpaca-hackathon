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


def _fp(a=None, b=None, variant=None, dirty=False) -> dict:
    """A collect() result, with only the fields warnings() reads. Keyed by the
    lineup fingerprint_run compares: the replicate pair and the variant arm."""
    base = {"model": "m", "take_profit_pct": 60, "strategy_notes_sha256": "abc123"}
    return {
        "created_utc": "2026-09-07T20:00:00+00:00",
        "git": {"sha": "0" * 40, "subject": "x", "dirty": dirty},
        "files": {},
        "effective_config": {
            fingerprint_run.PAIR[0]: {**base, **(a or {})},
            fingerprint_run.PAIR[1]: {**base, **(b or {})},
            fingerprint_run.VARIANT: {**base, **(variant or {})},
        },
    }


EQUAL_EQUITY = {"base_a": 100000.0, "base_b": 100000.0, "base_mixed": 100000.0}


def _names(fp, equity="default") -> list[str]:
    """Equal opening equity by default: a test about the replicate pair or a
    flatten date should not also trip the equity warning. Pass None to test
    the absence of equity itself."""
    eq = EQUAL_EQUITY if equity == "default" else equity
    return [w["warning"] for w in fingerprint_run.warnings(fp, eq)]


def _clean_collect(mod):
    """collect() with dirty forced false - adopt_sha refuses on a dirty tree,
    and the repo is dirty while these very tests are being written."""
    real = mod.collect

    def fake():
        out = real()
        out["git"]["dirty"] = False
        return out
    return fake


class Warnings(unittest.TestCase):
    def test_the_clean_case_is_silent(self):
        self.assertEqual(fingerprint_run.warnings(_fp(), EQUAL_EQUITY), [])

    def test_dirty_tree(self):
        self.assertIn("uncommitted_changes", _names(_fp(dirty=True)))

    def test_a_broken_replicate_pair_is_caught(self):
        """The run's whole value is that official and test start identical."""
        w = fingerprint_run.warnings(_fp(b={"take_profit_pct": 55}), EQUAL_EQUITY)
        self.assertEqual([x["warning"] for x in w], ["replicate_pair_broken"])
        self.assertIn("take_profit_pct", w[0]["detail"])

    def test_a_key_present_on_only_one_side_breaks_the_pair(self):
        """An absent key is not a neutral key: bot/risk.py reads several
        through config.get(key, DEFAULT), so a config that omits one runs the
        code default silently. A retired variant shipped a 30-minute exit
        leash that way (#269) while every live account ran 0."""
        fp = _fp()
        del fp["effective_config"][fingerprint_run.PAIR[1]]["take_profit_pct"]
        self.assertIn("replicate_pair_broken", _names(fp))

    def test_mixed_may_differ_in_prose_only(self):
        prose_only = _fp(variant={"strategy_notes_sha256": "different", "instrument_note_sha256": "n"})
        self.assertEqual(fingerprint_run.warnings(prose_only, EQUAL_EQUITY), [])

    def test_mixed_differing_in_a_real_value_is_caught(self):
        w = fingerprint_run.warnings(_fp(variant={"take_profit_pct": 70}), EQUAL_EQUITY)
        self.assertEqual([x["warning"] for x in w], ["mixed_is_not_prose_only"])
        self.assertIn("take_profit_pct", w[0]["detail"])

    def test_a_past_final_flatten_date_is_caught(self):
        """run_cycle.py refuses every new entry on or after this date, so an
        account carrying an old one is parked and will look like a strategy
        that stopped trading."""
        past = {"final_flatten_date": "2020-01-01"}
        w = fingerprint_run.warnings(_fp(a=past, b=past, variant=past), EQUAL_EQUITY)
        self.assertIn("final_flatten_date_in_the_past", [x["warning"] for x in w])

    def test_a_future_final_flatten_date_is_fine(self):
        future = {"final_flatten_date": "2099-01-01"}
        self.assertEqual(fingerprint_run.warnings(_fp(a=future, b=future, variant=future), EQUAL_EQUITY), [])

    def test_an_empty_final_flatten_date_is_fine(self):
        """The live configs cleared this key after the competition; None must
        not read as 'a date in the past'."""
        blank = {"final_flatten_date": None}
        self.assertEqual(fingerprint_run.warnings(_fp(a=blank, b=blank, variant=blank), EQUAL_EQUITY), [])


class OpeningEquity(unittest.TestCase):
    """With absolute position caps the starting equity IS the leverage. The
    2026-09-07 bundle reported no warnings while its pair was 13% apart,
    because nothing had ever looked. These are that check."""

    def test_silence_about_equity_is_itself_a_warning(self):
        self.assertIn("opening_equity_not_recorded", _names(_fp(), None))

    def test_equal_equity_is_silent(self):
        self.assertEqual(fingerprint_run.warnings(_fp(), EQUAL_EQUITY), [])

    def test_unequal_equity_across_the_pair_is_caught(self):
        eq = dict(EQUAL_EQUITY, base_b=92938.91)
        w = [x for x in fingerprint_run.warnings(_fp(), eq) if x["warning"] == "unequal_opening_equity"]
        self.assertEqual(len(w), 1)
        self.assertIn("leverage difference", w[0]["detail"])

    def test_the_judged_week_numbers_would_have_been_caught(self):
        """The exact pair that made the old bundle wrong: $105,095.51 against
        $92,938.91 is a 13% difference, and it should read as such."""
        eq = {"base_a": 105095.51, "base_b": 92938.91, "base_mixed": 99820.15}
        w = [x for x in fingerprint_run.warnings(_fp(), eq) if x["warning"] == "unequal_opening_equity"]
        self.assertIn("13.1%", w[0]["detail"])

    def test_a_missing_account_is_distinguished_from_unequal(self):
        eq = {"base_a": 100000.0}
        got = [x["warning"] for x in fingerprint_run.warnings(_fp(), eq)]
        self.assertIn("opening_equity_incomplete", got)
        self.assertNotIn("unequal_opening_equity", got)

    def test_parse_equity(self):
        self.assertEqual(
            fingerprint_run.parse_equity("base_a=100000,base_b=100000.50"),
            {"base_a": 100000.0, "base_b": 100000.50},
        )
        self.assertEqual(fingerprint_run.parse_equity(""), {})


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
            out = fingerprint_run.create("r", "the question", "the hypothesis", "base_a,base_b")
            names = sorted(p.name for p in out.iterdir())
            spec = json.loads((out / "strategy_spec.json").read_text())
        self.assertEqual(names, ["data_fingerprint.json", "notes.md", "strategy_spec.json", "warnings.json"])
        self.assertEqual(spec["commitment"], "rules fixed before results")
        self.assertEqual(spec["question"], "the question")
        self.assertEqual(spec["accounts"], ["base_a", "base_b"])

    def test_check_is_clean_immediately_after_create(self):
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            fingerprint_run.create("r", "q", "h", "base_a")
            self.assertEqual(fingerprint_run.check("r"), 0)

    def test_check_fails_when_a_fingerprinted_file_changed(self):
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            fingerprint_run.create("r", "q", "h", "base_a")
            stored = json.loads((Path(tmp) / "r" / "data_fingerprint.json").read_text())
            stored["files"]["config.yaml"]["sha256"] = "0" * 64
            (Path(tmp) / "r" / "data_fingerprint.json").write_text(json.dumps(stored))
            self.assertEqual(fingerprint_run.check("r"), 1)

    def test_check_fails_when_an_input_appeared(self):
        """_inputs() globs bot/*.py and config-variants/*.yaml, so a file
        appearing there is a new module or a new account - a mid-run code
        change, not noise."""
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            fingerprint_run.create("r", "q", "h", "base_a")
            fp = Path(tmp) / "r" / "data_fingerprint.json"
            stored = json.loads(fp.read_text())
            stored["files"].pop("config.yaml")          # as if it had not existed at creation
            fp.write_text(json.dumps(stored))
            self.assertEqual(fingerprint_run.check("r"), 1)

    def test_adopt_sha_refuses_when_inputs_moved(self):
        """The pointer may move; the commitment may not. Adopting over a
        changed input would launder exactly the event the bundle exists to
        expose."""
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            fingerprint_run.create("r", "q", "h", "base_a")
            fp = Path(tmp) / "r" / "data_fingerprint.json"
            stored = json.loads(fp.read_text())
            stored["files"]["config.yaml"]["sha256"] = "0" * 64
            fp.write_text(json.dumps(stored))
            self.assertEqual(fingerprint_run.adopt_sha("r"), 1)

    def test_adopt_sha_keeps_the_old_sha(self):
        """An audit trail that erases what it replaced is not one."""
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            fingerprint_run.create("r", "q", "h", "base_a")
            fp = Path(tmp) / "r" / "data_fingerprint.json"
            stored = json.loads(fp.read_text())
            orphan = "a" * 40
            stored["git"]["sha"] = orphan
            fp.write_text(json.dumps(stored))
            with patch.object(fingerprint_run, "collect", _clean_collect(fingerprint_run)):
                rc = fingerprint_run.adopt_sha("r")
            after = json.loads(fp.read_text())
        self.assertEqual(rc, 0)
        self.assertIn(orphan, after["superseded_shas"])
        self.assertNotEqual(after["git"]["sha"], orphan)

    def test_check_on_a_missing_bundle_is_distinct_from_drift(self):
        """2, not 1: "you never registered this" and "it drifted" are
        different answers and a caller may want to treat them differently."""
        with TemporaryDirectory() as tmp, patch.object(fingerprint_run, "RUNS", Path(tmp)):
            self.assertEqual(fingerprint_run.check("never-created"), 2)


if __name__ == "__main__":
    unittest.main()

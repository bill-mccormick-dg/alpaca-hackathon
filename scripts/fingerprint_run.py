#!/usr/bin/env python3
"""Pre-register a run: what we expect, and exactly what it started from.

The cheap half of backlog item 24. A study you can only interpret after
seeing its result is not a study, and "was anything changed mid-run?" is a
question we have had no way to answer - the Sep 8 baseline is the first
period where it matters, because its whole value is that three accounts
started identical.

Two modes, and the second is the one that pays:

    python scripts/fingerprint_run.py --run baseline-2026-09-08 \\
        --hypothesis "..." --question "..."     # create the bundle
    python scripts/fingerprint_run.py --run baseline-2026-09-08 --check
                                                # re-hash, report drift

Writes runs/<name>/ - four files, the shape the survey's wheel/doa-parent
reads described:

  strategy_spec.json   the question, the accounts, and the commitment
                       "rules fixed before results", with the hypothesis
  data_fingerprint.json sha256 + byte count of every input, plus git SHA
  warnings.json        what was already true and would weaken the run
  notes.md             the same thing for humans

Deliberately committed to git, not written to logs/. The point of a
pre-registration is that its timestamp is checkable by someone else, and a
file in .gitignore proves nothing about when it was written.

--check exits 1 when any fingerprinted input has changed, so it can be a
cron line or a pre-analysis gate rather than something you remember to run.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RUNS = REPO / "runs"

# The three accounts cron actually runs (scripts/verify_models.py::CONFIGS
# is the same list). Anything else under config-variants/ is a local farm
# variant, not a scheduled account: fingerprinted, but not compared.
LIVE_CONFIGS = {
    "official": "config.yaml",
    "test": "config-test.yaml",
    "mixed": "config-variants/mixed.yaml",
}

# Prose keys are compared by hash, not value: they are long, and what matters
# is whether they moved, not what they say.
PROSE_KEYS = ("strategy_notes", "instrument_note")


def _inputs() -> list[Path]:
    """Everything that turns into behaviour. The git SHA below covers the
    committed tree, but hashing the files too catches a hand-edit on the
    trading host, which leaves the SHA untouched and is exactly the failure
    a mid-run change would look like."""
    paths = [REPO / p for p in LIVE_CONFIGS.values()]
    paths += sorted((REPO / "config-variants").glob("*.yaml"))
    paths += sorted((REPO / "bot").glob("*.py"))
    paths += [REPO / "run_cycle.py", REPO / "flatten.py", REPO / "eod_review.py", REPO / "requirements.txt"]
    seen, out = set(), []
    for p in paths:
        if p.exists() and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _rel(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise - RUNS is not always
    under REPO (a test patches it, and a caller may point it anywhere), and
    a path helper must not be the thing that raises."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _digest(path: Path) -> dict:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _effective(path: Path) -> dict:
    """Config values as loaded, prose replaced by its hash."""
    raw = yaml.safe_load(path.read_text()) or {}
    out = {k: v for k, v in raw.items() if k not in PROSE_KEYS}
    for k in PROSE_KEYS:
        if k in raw:
            out[f"{k}_sha256"] = hashlib.sha256(str(raw[k]).encode()).hexdigest()[:16]
    return out


def collect() -> dict:
    return {
        "created_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "git": {
            "sha": _git("rev-parse", "HEAD"),
            "subject": _git("log", "-1", "--format=%s"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "files": {str(p.relative_to(REPO)): _digest(p) for p in _inputs()},
        "effective_config": {a: _effective(REPO / f) for a, f in LIVE_CONFIGS.items()},
    }


def warnings(fp: dict) -> list[dict]:
    """Things already true that would weaken the run. Recorded rather than
    fixed: a pre-registration that quietly cleans up its own inputs is not
    recording the run that happened."""
    out = []
    if fp["git"]["dirty"]:
        out.append({
            "warning": "uncommitted_changes",
            "detail": "the working tree was dirty when this bundle was written; the git SHA does not "
                      "fully describe what ran",
        })

    # The replicate pair is the run's whole value: any differing effective
    # value between official and test means it is already broken.
    cfgs = fp["effective_config"]
    keys = set(cfgs["official"]) | set(cfgs["test"])
    differing = sorted(k for k in keys if cfgs["official"].get(k) != cfgs["test"].get(k))
    if differing:
        out.append({
            "warning": "replicate_pair_broken",
            "detail": f"official and test differ in {len(differing)} effective value(s): {differing}",
        })

    # mixed is meant to be a single-variable arm: prose only.
    numeric = sorted(
        k for k in set(cfgs["official"]) | set(cfgs["mixed"])
        if not k.endswith("_sha256") and cfgs["official"].get(k) != cfgs["mixed"].get(k)
    )
    if numeric:
        out.append({
            "warning": "mixed_is_not_prose_only",
            "detail": f"mixed differs from official in non-prose value(s): {numeric}",
        })

    # A wind-down date already in the past means that account cannot open a
    # position at all (run_cycle.py: "final day - no new entries").
    today = date.today().isoformat()  # noqa: DTZ011 - a calendar date, compared to a config string
    for account, cfg in cfgs.items():
        final = cfg.get("final_flatten_date")
        if final and str(final) <= today:
            out.append({
                "warning": "final_flatten_date_in_the_past",
                "detail": f"{account} has final_flatten_date={final}; it will refuse every new entry",
            })
    return out


def create(run: str, question: str, hypothesis: str, accounts: str) -> Path:
    out = RUNS / run
    out.mkdir(parents=True, exist_ok=True)
    fp = collect()
    warns = warnings(fp)

    spec = {
        "run": run,
        "question": question,
        "hypothesis": hypothesis,
        "accounts": accounts.split(","),
        # The line that makes this a pre-registration rather than a summary.
        "commitment": "rules fixed before results",
        "created_utc": fp["created_utc"],
        "git_sha": fp["git"]["sha"],
    }
    (out / "strategy_spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    (out / "data_fingerprint.json").write_text(json.dumps(fp, indent=2) + "\n")
    (out / "warnings.json").write_text(json.dumps(warns, indent=2) + "\n")
    (out / "notes.md").write_text(_notes(spec, fp, warns))
    return out


def _notes(spec: dict, fp: dict, warns: list[dict]) -> str:
    lines = [
        f"# {spec['run']}",
        "",
        f"Written **{spec['created_utc']}**, before the run produced anything.",
        f"Commitment: *{spec['commitment']}*.",
        "",
        "## The question",
        "",
        spec["question"],
        "",
        "## The hypothesis, stated in advance",
        "",
        spec["hypothesis"],
        "",
        "## What it started from",
        "",
        f"- git `{fp['git']['sha'][:12]}` — {fp['git']['subject']}"
        + (f" (re-pointed from {', '.join(s[:12] for s in fp['superseded_shas'])}, orphaned by a squash-merge;"
           " every input was verified byte-identical first)" if fp.get("superseded_shas") else ""),
        f"- accounts: {', '.join(spec['accounts'])}",
        f"- {len(fp['files'])} input files fingerprinted (sha256 in `data_fingerprint.json`)",
        "",
        "Re-check at any time, and before analysing anything:",
        "",
        "```sh",
        f"python scripts/fingerprint_run.py --run {spec['run']} --check",
        "```",
        "",
        "## Warnings at creation",
        "",
    ]
    if warns:
        lines += [f"- **{w['warning']}** — {w['detail']}" for w in warns]
    else:
        lines.append("None. Every input was committed and the accounts were configured as intended.")
    lines += [
        "",
        "---",
        "",
        "If the result contradicts the hypothesis above, that is the finding, and it gets",
        "published as it stands. Deciding what we expected after seeing the number is the",
        "failure this file exists to prevent.",
        "",
    ]
    return "\n".join(lines)


def _load(run: str) -> dict | None:
    stored_path = RUNS / run / "data_fingerprint.json"
    if not stored_path.exists():
        print(f"no fingerprint at {_rel(stored_path)} - create it first", file=sys.stderr)
        return None
    return json.loads(stored_path.read_text())


def _diff(stored: dict, now: dict) -> tuple[list, list, list, list]:
    """(changed, missing, added, config drift). The git SHA is deliberately
    NOT part of this: it is a pointer, and the commitment is the file hashes
    and the hypothesis. See adopt_sha()."""
    changed, missing = [], []
    for name, d in stored["files"].items():
        cur = now["files"].get(name)
        if cur is None:
            missing.append(name)
        elif cur["sha256"] != d["sha256"]:
            changed.append(name)
    added = [n for n in now["files"] if n not in stored["files"]]

    drift = []
    for account, cfg in stored["effective_config"].items():
        cur = now["effective_config"].get(account, {})
        for key in sorted(set(cfg) | set(cur)):
            if cfg.get(key) != cur.get(key):
                drift.append((account, key, cfg.get(key, "<absent>"), cur.get(key, "<absent>")))
    return changed, missing, added, drift


def adopt_sha(run: str) -> int:
    """Re-point a bundle at a reachable commit after a squash-merge.

    A bundle created on a branch records that branch's SHA, and a squash
    merge orphans it: `git merge-base --is-ancestor` says no, and anyone
    cloning the repo cannot check out the tree the bundle claims to
    describe. That silently breaks the one property a pre-registration
    exists to have.

    What is being re-pointed is only the pointer. The commitment is the
    hypothesis and the file hashes, and this refuses to run unless every
    one of those still matches byte for byte - so it can move the SHA to a
    commit with identical content and cannot launder a changed input. The
    old SHA is kept in superseded_shas.

    ORDERING MATTERS: run this AFTER the bundle has landed on the branch you
    want it to point at, not before. Adopting on a feature branch records
    that branch's SHA, which the next squash orphans the same way - the fix
    chases its own tail. The sequence is: merge, then adopt on the target
    branch, then commit the re-pointed bundle. That last commit does not
    itself need adopting, because the SHA it names is already permanent.
    """
    stored = _load(run)
    if stored is None:
        return 2
    now = collect()
    changed, missing, added, drift = _diff(stored, now)
    if changed or missing or added or drift:
        print("refusing: the tree does not match the fingerprint, so this would be rewriting "
              "history rather than re-pointing it. Run --check.", file=sys.stderr)
        return 1
    if now["git"]["dirty"]:
        print("refusing: working tree is dirty; the adopted SHA would not describe it.", file=sys.stderr)
        return 1
    old = stored["git"]["sha"]
    if old == now["git"]["sha"]:
        print(f"already at {old[:12]} - nothing to adopt")
        return 0

    stored.setdefault("superseded_shas", []).append(old)
    stored["git"] = now["git"]
    stored["sha_adopted_utc"] = now["created_utc"]
    d = RUNS / run
    (d / "data_fingerprint.json").write_text(json.dumps(stored, indent=2) + "\n")

    spec = json.loads((d / "strategy_spec.json").read_text())
    spec.setdefault("superseded_shas", []).append(spec.get("git_sha"))
    spec["git_sha"] = now["git"]["sha"]
    (d / "strategy_spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    (d / "notes.md").write_text(_notes(spec, stored, json.loads((d / "warnings.json").read_text())))

    print(f"adopted {now['git']['sha'][:12]} (was {old[:12]}, orphaned)")
    print("inputs verified identical first - the commitment is unchanged")
    return 0


def check(run: str) -> int:
    stored = _load(run)
    if stored is None:
        return 2
    now = collect()
    changed, missing, added, drift = _diff(stored, now)

    print(f"run {run}, fingerprinted {stored['created_utc']} at git {stored['git']['sha'][:12]}")
    print(f"now git {now['git']['sha'][:12]}{' (dirty)' if now['git']['dirty'] else ''}")
    print(f"{len(stored['files'])} files checked: {len(changed)} changed, {len(missing)} missing, {len(added)} added")

    for name in changed:
        print(f"  CHANGED  {name}")
    for name in missing:
        print(f"  MISSING  {name}")
    for name in added:
        print(f"  ADDED    {name}")

    # Effective config is reported separately: a bot/*.py change and a config
    # value change are very different kinds of mid-run event.
    if drift:
        print(f"\n{len(drift)} effective config value(s) moved since the fingerprint:")
        for account, key, was, now_val in drift:
            print(f"  {account}.{key}: {was} -> {now_val}")
    else:
        print("\nno effective config values moved")

    if changed or missing or added or drift:
        print("\nThe run did not start from what it is running on. Say so in the write-up.", file=sys.stderr)
        return 1
    print("\nclean - inputs match the pre-registration")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run name, e.g. baseline-2026-09-08")
    ap.add_argument("--check", action="store_true", help="re-hash and report drift; exits 1 on any change")
    ap.add_argument("--adopt-sha", action="store_true",
                    help="re-point the bundle at HEAD after a squash-merge orphaned its SHA; "
                         "refuses unless every fingerprinted input is byte-identical")
    ap.add_argument("--question", default="", help="what the run is meant to answer")
    ap.add_argument("--hypothesis", default="", help="what we expect, written before the result exists")
    ap.add_argument("--accounts", default="official,test,mixed")
    args = ap.parse_args()

    if args.check:
        return check(args.run)
    if args.adopt_sha:
        return adopt_sha(args.run)
    if not args.question or not args.hypothesis:
        ap.error("--question and --hypothesis are required when creating a bundle; "
                 "a pre-registration with nothing registered is decoration")
    out = create(args.run, args.question, args.hypothesis, args.accounts)
    warns = json.loads((out / "warnings.json").read_text())
    print(f"wrote {_rel(out)}/ - {len(json.loads((out / 'data_fingerprint.json').read_text())['files'])} files fingerprinted")
    for w in warns:
        print(f"  WARNING {w['warning']}: {w['detail']}")
    if not warns:
        print("  no warnings")
    print("\nCommit it before the run starts - that is what makes the timestamp mean anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

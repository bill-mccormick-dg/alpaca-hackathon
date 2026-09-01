#!/usr/bin/env python3
"""Offline re-check of a day's prior citations (#172).

Cycles that ran before the audit was journaled have no `citations` field;
this rebuilds it from the journal alone by pairing each `decision` with the
last `predictions` record before it (predictions is written before
cycle_start, so that is the same cycle's prior) and re-parsing the model's
raw output the way bot/decide.py does. Read-only; no credentials.

    python scripts/audit_citations.py --account official --day 2026-09-01
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import citations, journal
from bot.decide import _extract_json_array, _parse_proposal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default="official")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()
    journal.use_account(args.account)

    prior, checked, unsupported, misattributed, skipped = None, 0, [], [], 0
    for r in journal.read_events(args.day, events=("predictions", "decision")):
        if r["event"] == "predictions":
            prior = r
            continue
        try:
            proposals = [_parse_proposal(a) for a in _extract_json_array(str(r.get("raw") or "[]")) if isinstance(a, dict)]
        except ValueError:
            continue
        result = citations.audit(proposals, prior, r.get("tool_calls"))
        if not result:
            continue
        if result.get("skipped"):
            skipped += 1
            continue
        checked += result["checked"]
        for u in result["unsupported"]:
            unsupported.append((str(r.get("ts"))[11:16], u))
            print(f"{str(r.get('ts'))[11:16]}  {u['symbol']:<22} quoted {u['quoted']:>6}   UNSUPPORTED - nearest: {u['nearest']['label']} {u['nearest']['value']}")
        for u in result.get("misattributed") or []:
            misattributed.append((str(r.get("ts"))[11:16], u))
            print(f"{str(r.get('ts'))[11:16]}  {u['symbol']:<22} quoted {u['quoted']:>6}   misattributed - it is {u['nearest']['label']}")
    print(f"\n{args.account} {args.day}: {checked} citation(s) checked, {len(unsupported)} unsupported, "
          f"{len(misattributed)} misattributed, {skipped} cycle(s) skipped (research tools ran)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

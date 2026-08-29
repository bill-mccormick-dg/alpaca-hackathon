#!/usr/bin/env bash
# The screen-recorded part of the video, as a runnable script (issue #23).
# Run on CT 108 during market hours in a 1600x900 terminal, font >= 16pt:
#   ssh root@192.168.212.10 'cd /opt/alpaca-hackathon && bash submission/video/demo.sh'
# It runs the real commands against the TEST account (never the official
# one), prints a caption before each, and pauses for narration (Enter).
# Use `PAUSE=0` to run through without stopping when rehearsing.
set -u
cd "${REPO:-$(dirname "$0")/../..}"
export PYTHONDONTWRITEBYTECODE=1
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python
ACCT=${ACCT:-test}
CFG=${CFG:-config-test.yaml}
PAUSE=${PAUSE:-1}
FORCE=${FORCE:-}   # set FORCE=--force to rehearse outside market hours

say()  { printf '\n\033[1;36m# %s\033[0m\n' "$*"; }
run()  { printf '\033[1;33m$ %s\033[0m\n' "$*"; "$@" 2>&1 | grep -v -E '^\[|FastMCP|│|╭|╰|Server|transport|^\s*$'; }
wait_() { [ "$PAUSE" = "1" ] && read -r -p $'\033[2m(enter)\033[0m' _ || true; }
[ -t 1 ] && clear

say "Shot 1 - one live cycle: snapshot -> research -> proposal -> risk gate"
run $PY run_cycle.py --account "$ACCT" --config "$CFG" --dry-run --verbose $FORCE
wait_

say "Shot 2 - what the account looks like right now"
run $PY status.py --account "$ACCT"
wait_

say "Shot 3 - every decision and tool call is journaled (last 5 events)"
run tail -n 5 "logs/journal-$ACCT.jsonl"
wait_

say "Shot 4 - the leash: close everything, verified against the broker, and trip the kill switch"
run $PY flatten.py --account "$ACCT" --halt
run $PY run_cycle.py --account "$ACCT" --config "$CFG"
run ls -la logs/HALT
run rm -f logs/HALT
wait_

say "Shot 5 - end-of-day: round trips from Alpaca's fills, rejections by rule, the model's own read"
run $PY eod_review.py --account "$ACCT" --no-model
wait_

say "Shot 6 - change one thing for tomorrow without a deploy (expires at the close)"
run $PY override.py --account "$ACCT" set take_profit_pct 50
run $PY override.py --account "$ACCT" show
run $PY override.py --account "$ACCT" clear --all
say "done"

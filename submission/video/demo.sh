#!/usr/bin/env bash
# The screen-recorded part of the video, as a runnable script (issue #23).
#
# Run on CT 108 during market hours in a 1600x900 terminal, font >= 16pt:
#   ssh root@<the-bot-host> 'cd /opt/alpaca-hackathon && bash submission/video/demo.sh'
#
# It runs the real commands, prints a caption before each, and pauses for
# narration (Enter). Anything destructive uses the TEST account; the judged
# account appears only through read-only commands.
#
#   PAUSE=0        rehearse without stopping
#   FORCE=--force  rehearse outside market hours
#   SKIP_HALT=1    rehearse without flattening the test account (shot 4 closes
#                  real positions and trips a real kill switch - fine on camera,
#                  not something to do twice by accident before the take)
#
# Four shots are a BROWSER, not this terminal. The script stops and tells you
# when to switch windows - have these open on another desktop beforehand:
#   1. https://bot.wpmccormick.pw          the live journal viewer
#   2. Home Assistant, the operator dashboard
#   3. Home Assistant, the read-only team dashboard
#   4. github.com/bill-mccormick-dg/alpaca-hackathon/actions   (deploy runs)
set -u
cd "${REPO:-$(dirname "$0")/../..}"
export PYTHONDONTWRITEBYTECODE=1
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python
ACCT=${ACCT:-test}
CFG=${CFG:-config-test.yaml}
PAUSE=${PAUSE:-1}
FORCE=${FORCE:-}
SKIP_HALT=${SKIP_HALT:-0}

say()   { printf '\n\033[1;36m# %s\033[0m\n' "$*"; }
cut_()  { printf '\n\033[1;35m### SWITCH TO THE BROWSER: %s\033[0m\n' "$*"; }
# The MCP server prints a banner and a version nag to stderr on every start.
# Two greps, not one alternation: the box-drawing characters inside an -E
# alternation depend on the locale being UTF-8, and the runner's is not.
clean() { grep -v -E '^\[|FastMCP|Server|transport|^[[:space:]]*$' | grep -v '[│╭╰🎉🖥🚀]'; }
run()   { printf '\033[1;33m$ %s\033[0m\n' "$*"; "$@" 2>&1 | clean; }
# Commands that never start the MCP server need no filtering - and must not be
# filtered, because clean() drops blank lines and these use them to breathe.
raw()   { printf '\033[1;33m$ %s\033[0m\n' "$*"; "$@" 2>&1; }
wait_() { [ "$PAUSE" = "1" ] && read -r -p $'\033[2m(enter)\033[0m' _ || true; }
[ -t 1 ] && clear

# ---------------------------------------------------------------- the agent
say "Shot 1 - one live cycle: gates, snapshot, the model, then the risk gate"
run $PY run_cycle.py --account "$ACCT" --config "$CFG" --dry-run --verbose $FORCE
wait_

say "Shot 2 - what the judged account was shown last cycle, and what it did with it"
raw $PY scripts/last_cycle.py --account official
wait_

say "Shot 3 - the account right now: positions, halt state, today's summary"
run $PY status.py --account "$ACCT"
wait_

# ---------------------------------------------------------------- the leash
say "Shot 4 - the kill switch: close everything, verify against the broker, refuse to run"
if [ "$SKIP_HALT" = "1" ]; then
  echo "  (skipped: SKIP_HALT=1)"
else
  run $PY flatten.py --account "$ACCT" --halt
  run $PY run_cycle.py --account "$ACCT" --config "$CFG"
  raw ls -la logs/HALT_manual_"$ACCT"
  run rm -f logs/HALT_manual_"$ACCT"
fi
wait_

# ---------------------------------------------------------------- the loop
say "Shot 5 - end of day: round trips, rejections by rule, and the priors graded against reality"
# Stop at the per-cycle transcript: on a quiet day that is forty lines of `[]`,
# and the numbers above it are the point. The full digest is written to
# logs/eod/<date>-<account>.md either way.
YDAY=$(date -d yesterday +%F 2>/dev/null || date -v-1d +%F)
printf '\033[1;33m$ %s\033[0m\n' "$PY eod_review.py --account official --date $YDAY --no-model"
$PY eod_review.py --account official --date "$YDAY" --no-model 2>&1 \
  | clean | sed '/## Model output/q'
wait_

say "Shot 6 - change one thing for tomorrow, without a deploy (expires at the close)"
raw $PY override.py --account "$ACCT" set take_profit_pct 50
raw $PY override.py --account "$ACCT" show
raw $PY override.py --account "$ACCT" clear --all
wait_

# ---------------------------------------------------------------- it ships itself
say "Shot 7 - what is actually running on this box, and how it got here"
# DEPLOYED, not `git log`: Ansible owns the checkout's .git and CI rsyncs files
# in with --exclude='.git', so `git log` here reports whatever Ansible last
# checked out - several commits stale. This marker is written by the job that
# actually deploys, which is why it is the one to put on camera.
raw cat DEPLOYED
say "written by the deploy job itself - the runner that ran it lives on this container:"
raw systemctl is-active actions.runner.bill-mccormick-dg-alpaca-hackathon.ct108-alpaca-hackathon.service
raw systemctl is-active alpaca-hackathon-mqtt-bridge alpaca-hackathon-journal-viewer
wait_

# ---------------------------------------------------------------- browser cuts
cut_ "https://bot.wpmccormick.pw - the live journal viewer"
echo "  Show: a cycle arriving live; the model's full reasoning; a BLOCKED line with"
echo "  its rule; the account checkboxes; the date picker replaying yesterday."
wait_

cut_ "Home Assistant - the operator dashboard"
echo "  Show: equity and day P&L per account, the last decision, the model selector,"
echo "  then the kill switch. Then the read-only team dashboard: no controls at all."
wait_

cut_ "GitHub Actions - the deploy history"
echo "  Show: green deploys back through the week, then one red X - a trading-code"
echo "  merge refused because the market was open."
wait_

say "done - stop the recording"

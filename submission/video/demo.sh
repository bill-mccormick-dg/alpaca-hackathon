#!/usr/bin/env bash
# The terminal half of the video, as a runnable script (issue #23).
#
# Runs on CT 108 during market hours in a 1600x900 terminal, font >= 16pt:
#   ssh root@<the-bot-host> 'cd /opt/alpaca-hackathon && bash submission/video/demo.sh'
#
# Real commands, a caption before each, a pause for narration (Enter). Anything
# destructive uses the TEST account; the judged account appears only through
# read-only commands.
#
#   demo.sh              every shot, in order
#   demo.sh 1 2          only those shots - record.sh drives it this way so it
#                        can put the browser on screen in between
#
#   PAUSE=0        no pauses - rehearsing
#   FORCE=--force  rehearse outside market hours
#   SKIP_HALT=1    rehearse without flattening the test account (shot 4 closes
#                  real positions and trips a real kill switch - fine on camera,
#                  not something to do twice by accident before the take)
#
# The browser shots are a separate script, because the browser is on your Mac
# and this is not: see browser.sh, and record.sh which drives both.
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
# The MCP server prints a banner and a version nag to stderr on every start.
# Two greps, not one alternation: the box-drawing characters inside an -E
# alternation depend on the locale being UTF-8, and the runner's is not.
clean() { grep -v -E '^\[|FastMCP|Server|transport|^[[:space:]]*$' | grep -v '[│╭╰🎉🖥🚀]'; }
run()   { printf '\033[1;33m$ %s\033[0m\n' "$*"; "$@" 2>&1 | clean; }
# Commands that never start the MCP server need no filtering - and must not be
# filtered, because clean() drops blank lines and these use them to breathe.
raw()   { printf '\033[1;33m$ %s\033[0m\n' "$*"; "$@" 2>&1; }
wait_() { [ "$PAUSE" = "1" ] && read -r -p $'\033[2m(enter)\033[0m' _ || true; }

shot1() {
  say "Shot 1 - one live cycle: gates, snapshot, the model, then the risk gate"
  run $PY run_cycle.py --account "$ACCT" --config "$CFG" --dry-run --verbose $FORCE
  wait_
}

shot2() {
  say "Shot 2 - what the judged account was shown last cycle, and what it did with it"
  raw $PY scripts/last_cycle.py --account official
  wait_
}

shot3() {
  say "Shot 3 - the account right now: positions, halt state, today's summary"
  run $PY status.py --account "$ACCT"
  wait_
}

shot4() {
  say "Shot 4 - the kill switch: close everything, verify against the broker, refuse to run"
  if [ "$SKIP_HALT" = "1" ]; then
    echo "  (skipped: SKIP_HALT=1)"
  else
    run $PY flatten.py --account "$ACCT" --halt
    run $PY run_cycle.py --account "$ACCT" --config "$CFG"
    raw ls -la logs/HALT_manual_"$ACCT"
    raw rm -f logs/HALT_manual_"$ACCT"
  fi
  wait_
}

shot5() {
  say "Shot 5 - end of day: round trips, rejections by rule, and the priors graded against reality"
  # Stop at the per-cycle transcript: on a quiet day that is forty lines of `[]`,
  # and the numbers above it are the point. The full digest is written to
  # logs/eod/<date>-<account>.md either way.
  YDAY=$(date -d yesterday +%F 2>/dev/null || date -v-1d +%F)
  printf '\033[1;33m$ %s\033[0m\n' "$PY eod_review.py --account official --date $YDAY --no-model"
  $PY eod_review.py --account official --date "$YDAY" --no-model 2>&1 \
    | clean | sed '/## Model output/q'
  wait_
}

shot6() {
  say "Shot 6 - change one thing for tomorrow, without a deploy (expires at the close)"
  raw $PY override.py --account "$ACCT" set take_profit_pct 50
  raw $PY override.py --account "$ACCT" show
  raw $PY override.py --account "$ACCT" clear --all
  wait_
}

shot7() {
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
}

if [ $# -eq 0 ]; then
  [ -t 1 ] && clear
  set -- 1 2 3 4 5 6 7
  ALL=1
fi
for n in "$@"; do
  case "$n" in
    [1-7]) "shot$n" ;;
    *) echo "no shot '$n' - shots are 1 to 7" >&2; exit 1 ;;
  esac
done
[ -n "${ALL:-}" ] && say "done - the browser shots are in browser.sh; record.sh drives both"
exit 0

#!/bin/bash
# The CLI half of a cycle capture - runs ON CT 108, fed over ssh by
# cycle_capture.sh (`ssh host bash -s -- HHMM <view> < cli_remote.sh`), so the
# vhs tape never has to quote any of this.
#
#   bash -s -- 1400 cycle        what run_cycle.py printed for the 14:00 cycle,
#                                verbatim from logs/cron-official.log - works
#                                for any past cycle, touches nothing
#   bash -s -- 1400 last_cycle   scripts/last_cycle.py - the inputs and the
#                                decision (the journal's LAST cycle, so live only)
#   bash -s -- 1400 status       status.py - positions, halt state, the day
#   bash -s -- 1400 facts        one line for cycles.txt, not for camera
#
# Everything here is read-only: no --dry-run (that journals a cycle), no
# eod_review (that journals too). The judged account only ever appears through
# things that read.
set -u
cd /opt/alpaca-hackathon || exit 1
export PYTHONDONTWRITEBYTECODE=1
t=${1:?HHMM}; view=${2:?view}
hh=${t:0:2}; mm=${t:2:2}
stamp="$(date +%m/%d/%y) $hh:$mm:"          # the MCP banner's timestamp prefix
LOG=logs/cron-official.log
PY=./.venv/bin/python

PACE=${PACE:-0.35}   # seconds per line on camera - the output is instant, the eye is not
clean() { grep -v -E '^\[|FastMCP|Server|transport|^[[:space:]]*$' | grep -v '[│╭╰🎉🖥🚀]'; }
pace()  { while IFS= read -r L; do printf '%s\n' "$L"; sleep "$PACE"; done; }
say()   { printf '\033[1;36m# %s\033[0m\n' "$*"; }
cmd()   { printf '\033[1;33m$ %s\033[0m\n' "$*"; }
block() { awk -v t="[$stamp" 'index($0,t)==1{p=1;next} p && /^\[[0-9][0-9]\/[0-9][0-9]\/[0-9][0-9] /{exit} p' "$LOG"; }
# The Python tools get </dev/null: this script arrives on stdin (`bash -s`), and
# the MCP server that status.py starts reads stdin - it would eat the rest of us.

case "$view" in
  cycle)
    say "Cycle $hh:$mm - what run_cycle.py printed, verbatim from the cron log"
    cmd "sed -n '/^\\[$stamp/,/^\\[/p' $LOG"
    block | clean | pace
    ;;
  last_cycle)
    say "Cycle $hh:$mm - what the judged account was shown, and what it did with it"
    cmd "$PY scripts/last_cycle.py --account official"
    $PY scripts/last_cycle.py --account official </dev/null 2>&1 | pace
    ;;
  status)
    say "Cycle $hh:$mm - the account right now: positions, halt state, today's summary"
    cmd "$PY status.py --account official"
    $PY status.py --account official </dev/null 2>&1 | clean | pace
    ;;
  facts)
    # what | equity | day_pnl | positions | ts_start | ts_decide | ts_order
    b=$(block | clean)
    eq=$(echo "$b" | grep -oE 'equity [0-9.]+' | head -1 | cut -d' ' -f2)
    pnl=$(echo "$b" | grep -oE 'day P&L [-+0-9.]+' | head -1 | awk '{print $3}')
    pos=$(echo "$b" | grep -oE 'positions [0-9]+' | head -1 | cut -d' ' -f2)
    what=$(echo "$b" | grep -E '^(SUBMITTED|REJECTED|EXIT|CANCELLED|decision:|DAILY|halted)' \
             | sed -E 's/ \(order [0-9a-f-]+\)//' | cut -c1-90 | paste -sd';' -)
    day=$(date +%F); j=logs/journal.jsonl
    et_h=$(( (10#$hh + 1) % 24 ))                      # CT -> ET, for the journal lookup
    pre=$(printf '"ts": "%sT%02d:%s' "$day" "$et_h" "$mm")
    # ...and back to CT for the row, so "14:00:09" reads as 9 s into a take that started 14:00:00
    ts() { grep "$pre" "$j" | grep "\"event\": \"$1\"" | head -1 | grep -o '"ts": "[^"]*"' | cut -d'"' -f4 | cut -c12-19 \
           | awk -F: 'NF==3 {printf "%02d:%s:%s", ($1+23)%24, $2, $3}'; }
    printf '%s|%s|%s|%s|%s|%s|%s\n' "${what:-?}" "${eq:-?}" "${pnl:-?}" "${pos:-?}" "$(ts cycle_start)" "$(ts decision)" "$(ts order_submitted)"
    exit 0                                   # not for camera: no cursor games
    ;;
  *) echo "no view '$view'" >&2; exit 1 ;;
esac
printf '\033[?25l'

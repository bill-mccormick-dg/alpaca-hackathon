#!/usr/bin/env bash
# One take, both halves (issue #23).
#
# Runs on your MAC. The terminal shots live on CT 108 and the browser is here,
# so something has to sit in the middle and alternate: this. It ssh's for each
# terminal shot, drives Chrome for each slide and each web page, and follows
# narration.md's running order exactly.
#
#   bash submission/video/record.sh --check     # nothing recorded: is everything ready?
#   bash submission/video/record.sh             # the take
#   bash submission/video/record.sh --from 9    # pick up after a fluffed line
#   bash submission/video/record.sh --list      # the running order
#
# How the pacing works, because it is the only unusual part: a terminal step
# waits for Enter, in this window. A browser step puts Chrome in front and then
# waits for you to cmd-tab BACK here. So you talk for exactly as long as you
# want over either one, and nothing advances until you move. Recording captures
# the whole screen, so the switch itself is the cut.
#
# Requires: macOS, Chrome, ssh to the host, and Accessibility permission for
# this terminal (System Events reads which app is frontmost). Optional but
# nice: Chrome's View > Developer > "Allow JavaScript from Apple Events", which
# lets the Home Assistant shots fit the whole dashboard in frame by themselves.
set -u
cd "$(dirname "$0")"

HOST=${HOST:-root@192.168.212.10}
REMOTE=${REMOTE:-/opt/alpaca-hackathon}
SSH_OPTS=${SSH_OPTS:--o BatchMode=yes}
HA_ZOOM=${HA_ZOOM:-0.62}          # fits the state rows, graphs and controls at once
export PAUSE=${PAUSE:-1}

# step | kind | argument | what the narration is doing over it
STEPS=(
  "deck|1|title - 8s"
  "deck|2|the thesis in one sentence"
  "deck|3|one cycle: the runtime diagram"
  "shot|1|a live cycle on CT 108"
  "shot|2|what the model was shown, and what it did"
  "deck|4|Greeks: Alpaca's, Black-Scholes as the backstop"
  "deck|7|...and then we grade the prior"
  "shot|4|the kill switch"
  "web|viewer|the live journal viewer, and the four bugs it caught"
  "web|ha|Home Assistant: the operator dashboard"
  "web|ha-team|Home Assistant: the read-only team dashboard"
  "shot|5 7|the end-of-day digest, then what is actually deployed"
  "web|actions|the deploy history, green all week and one red X"
  "deck|17|results"
  "deck|18|thanks"
)

bar() { printf '\n\033[1;35m== %02d/%02d  %s\033[0m\n' "$1" "${#STEPS[@]}" "$2"; }

check() {
  local ok=0
  printf '\033[1m1. the host\033[0m\n'
  if ssh $SSH_OPTS "$HOST" "test -x $REMOTE/.venv/bin/python && cat $REMOTE/DEPLOYED | head -2" 2>/dev/null; then
    printf '   \033[32mok\033[0m - and that is the sha on camera\n'
  else
    printf '   \033[31mFAILED\033[0m - ssh %s\n' "$HOST"; ok=1
  fi

  printf '\n\033[1m2. yesterday has a digest to show (shot 5)\033[0m\n'
  local yday
  yday=$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F)
  if ssh $SSH_OPTS "$HOST" "grep -qc cycle_start $REMOTE/logs/journal.jsonl" 2>/dev/null; then
    printf '   \033[32mok\033[0m - official journal has cycles; shot 5 will use %s\n' "$yday"
  else
    printf '   \033[33mcheck\033[0m - no journal on the host?\n'
  fi

  printf '\n\033[1m3. accessibility (this terminal may read the frontmost app)\033[0m\n'
  if osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' >/dev/null 2>&1; then
    printf '   \033[32mok\033[0m\n'
  else
    printf '   \033[31mFAILED\033[0m - System Settings > Privacy & Security > Accessibility, add this terminal\n'; ok=1
  fi

  printf '\n\033[1m4. Chrome JavaScript from Apple Events (optional, fits the HA dashboard)\033[0m\n'
  if osascript -e 'tell application "Google Chrome" to execute front window'"'"'s active tab javascript "1"' >/dev/null 2>&1; then
    printf '   \033[32mok\033[0m\n'
  else
    printf '   \033[33moff\033[0m - Chrome menu: View > Developer > Allow JavaScript from Apple Events\n'
    printf '        without it, zoom the HA page by hand before its step\n'
  fi

  printf '\n\033[1m5. the browser tabs\033[0m\n'
  bash browser.sh setup || ok=1
  printf '\nAnything NOT READY above is a login. Do it now - the viewer'"'"'s one-time PIN\n'
  printf 'is slow on camera, and its session only lasts six hours.\n'
  printf '\nThen drag that Chrome window onto the display you are recording, and leave\n'
  printf 'it there. Every browser step reuses it.\n'
  return $ok
}

list() {
  local i=1
  for s in "${STEPS[@]}"; do
    IFS='|' read -r kind arg what <<< "$s"
    printf '  %2d  %-5s %-8s %s\n' "$i" "$kind" "$arg" "$what"
    i=$((i + 1))
  done
}

run_step() {
  IFS='|' read -r kind arg what <<< "$1"
  case "$kind" in
    deck) bash browser.sh deck "$arg" ;;
    web)
      case "$arg" in
        ha|ha-team) bash browser.sh show "$arg" "$HA_ZOOM" ;;
        *)          bash browser.sh show "$arg" ;;
      esac ;;
    shot)
      # shellcheck disable=SC2086 - arg is a deliberate word list ("5 7")
      ssh -t $SSH_OPTS "$HOST" "cd $REMOTE && PAUSE=$PAUSE SKIP_HALT=${SKIP_HALT:-0} FORCE=${FORCE:-} bash submission/video/demo.sh $arg" ;;
  esac
}

from=1
case "${1:-}" in
  --check) check; exit $? ;;
  --list)  list; exit 0 ;;
  --from)  from=${2:?which step}; ;;
  "") ;;
  *) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

[ -f "${TMPDIR:-/tmp}/alpaca-video-window" ] || bash browser.sh setup

printf '\n\033[1mStart the screen recording now.\033[0m Enter when it is rolling.\n'
read -r _

i=1
for s in "${STEPS[@]}"; do
  if [ "$i" -ge "$from" ]; then
    IFS='|' read -r _ _ what <<< "$s"
    bar "$i" "$what"
    run_step "$s"
  fi
  i=$((i + 1))
done

printf '\n\033[1;35m== done - stop the recording\033[0m\n'
printf 'Then: bash submission/video/browser.sh close\n'

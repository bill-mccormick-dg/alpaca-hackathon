#!/usr/bin/env bash
# The browser half of the recording, driven rather than fumbled (issue #23).
#
# Runs on the MAC, not on CT 108 - the browser is here. It opens one dedicated
# Chrome window with every tab the video needs, then puts the right one on
# screen when `record.sh` asks for it, so the take never includes somebody
# hunting through windows.
#
#   bash submission/video/browser.sh setup        # open the window, report readiness
#   bash submission/video/browser.sh show viewer  # bring that tab up, wait, return
#   bash submission/video/browser.sh deck 7       # deck to slide 7
#   bash submission/video/browser.sh close
#
# `show` and `deck` block while Chrome is in front, so you talk for as long as
# you like and the script resumes the moment you switch back. There is a four
# minute ceiling in case you forget.
#
# Requires: macOS, Google Chrome, and Accessibility permission for whichever
# terminal you run this from (System Events reads which app is in front).
# Optional: View > Developer > "Allow JavaScript from Apple Events" in Chrome,
# which lets this fit the Home Assistant dashboard to the window instead of
# you zooming by hand. Everything else works without it.
set -u

VIEWER=${VIEWER:-https://bot.wpmccormick.pw}
HA=${HA:-http://192.168.212.55:8123}
REPO_URL=${REPO_URL:-https://github.com/bill-mccormick-dg/alpaca-hackathon}
DECK=${DECK:-file://$(cd "$(dirname "$0")" && pwd)/slides.html}
WAIT_CEILING=${WAIT_CEILING:-240}
STATE=${STATE:-${TMPDIR:-/tmp}/alpaca-video-window}
# Sized for a 1080p capture. Set BOUNDS= (empty) to leave the window alone.
BOUNDS=${BOUNDS:-0, 0, 1600, 1000}

# tab order is fixed; `show` looks a name up in it
TABS=(deck viewer ha ha-team actions)
URLS=("$DECK" "$VIEWER" "$HA/alpaca-hackathon" "$HA/alpaca-hackathon-team" "$REPO_URL/actions/workflows/deploy.yml")

osa() { osascript "$@" 2>&1; }
front() { osa -e 'tell application "System Events" to get name of first process whose frontmost is true'; }
win() { [ -f "$STATE" ] && cat "$STATE" || { echo "no window - run: browser.sh setup" >&2; exit 1; }; }

index_of() {
  local i=1
  for t in "${TABS[@]}"; do [ "$t" = "$1" ] && { echo "$i"; return; }; i=$((i + 1)); done
  echo "unknown tab '$1' - one of: ${TABS[*]}" >&2
  exit 1
}

# Select a tab, bring its window forward, then CHECK that it worked.
#
# Chrome's `index` property looks like the way to raise a window and is a
# silent no-op in current versions; System Events AXRaise cannot see windows on
# another Space or display, which on a two-monitor desk is most of them. So the
# honest primitive is: ask, verify, and if the wrong window is in front, say so
# and let a human click once. Guessing here sends the deck's arrow keys into
# whatever window really had focus - which is what happened the first time this
# was written, on a second monitor.
raise() {
  local id idx want got
  id=$(win); idx=$1
  osa -e "tell application \"Google Chrome\"
    set w to window id $id
    set minimized of w to false
    set active tab index of w to $idx
    activate
  end tell" > /dev/null
  sleep 0.6
  want=$(osa -e "tell application \"Google Chrome\" to get title of tab $idx of window id $id")
  got=$(osa -e 'tell application "Google Chrome" to get title of active tab of front window')
  [ "$want" = "$got" ] && return 0

  printf '\033[1;33m   the recording window is not in front\033[0m (Chrome shows: %s)\n' "$got"
  printf '   click the "%s" window once, then press Enter here.\n' "$want"
  read -r _
}

# Hold while Chrome is the frontmost app; resume the moment the operator leaves
# it. Deliberately "not Chrome" rather than "back in the app that launched me":
# whichever terminal this runs from is not knowable, and if Chrome happened to
# be frontmost already the comparison would fire instantly and skip the shot.
# The settle sleep covers the same race the other way - `activate` is async.
hold() {
  local waited=0
  sleep 0.9
  while [ "$(front)" = "Google Chrome" ] && [ "$waited" -lt "$WAIT_CEILING" ]; do
    sleep 0.4
    waited=$((waited + 1))
  done
}

setup() {
  local id
  id=$(osa <<AS
tell application "Google Chrome"
  set w to make new window
  set URL of active tab of w to "${URLS[0]}"
$(for u in "${URLS[@]:1}"; do echo "  make new tab at end of tabs of w with properties {URL:\"$u\"}"; done)
  set active tab index of w to 1
  activate
  return id of w
end tell
AS
)
  case "$id" in ''|*[!0-9]*) echo "could not open the window: $id" >&2; exit 1 ;; esac
  echo "$id" > "$STATE"

  # Size it for a 1080p capture. Only the SIZE: Chrome opens a new window on
  # whichever display it last used and keeps it there, so an origin of 0,0 came
  # back as 2026,-39 on a four-monitor desk. Not worth fighting - the script
  # sizes the window, you put it where the recording is.
  if [ -n "$BOUNDS" ]; then
    sleep 0.5
    osa -e "tell application \"Google Chrome\" to set bounds of window id $id to {$BOUNDS}" > /dev/null
  fi
  echo "window $id, ${#TABS[@]} tabs: ${TABS[*]}"
  echo "drag it onto the display you are recording - every later step reuses this window"

  # Give the pages a moment, then say which ones are not ready. The viewer's
  # Cloudflare session lasts six hours and its email one-time PIN is slow on
  # camera, so finding out now is the whole point of this check.
  sleep 6
  # One title per line, built with a repeat rather than a list coercion: the
  # coercion needs AppleScript's text item delimiters, and an apostrophe inside
  # a heredoc inside $( ) derails bash's own parser. A page title containing a
  # comma would also shift every column if these were comma-joined.
  local titles i=1
  titles=$(osa <<AS
tell application "Google Chrome"
  set out to ""
  repeat with t in tabs of window id $id
    set out to out & (title of t) & linefeed
  end repeat
  return out
end tell
AS
)
  echo
  for t in "${TABS[@]}"; do
    local title
    title=$(echo "$titles" | sed -n "${i}p")
    case "$title" in
      *"Cloudflare Access"*|*"Sign in"*|*"Log in"*)
        printf '  \033[1;31mNOT READY\033[0m  %-8s %s\n     -> log in now, before you record\n' "$t" "$title" ;;
      *) printf '  \033[1;32mready\033[0m      %-8s %s\n' "$t" "$title" ;;
    esac
    i=$((i + 1))
  done
}

show() {
  raise "$(index_of "$1")"
  [ -n "${2:-}" ] && fit "$2"
  printf '\033[2m   ...on screen. cmd-tab back here when you are done talking.\033[0m\n'
  hold
}

# Optional polish: scale a page so the whole dashboard fits one frame. Silently
# does nothing when Chrome's AppleScript JavaScript switch is off.
fit() {
  osa -e "tell application \"Google Chrome\" to execute active tab of front window javascript \"document.body.style.zoom='$1'\"" > /dev/null
}

# Deck to slide N. The deck moves on arrow keys, so Home then N-1 rights lands
# on it from wherever it was - no state to keep in sync. Keys only go out after
# raise() has confirmed which window has focus, or they land somewhere else
# entirely. Note the deck also advances on a mouse click anywhere in the page,
# so try not to click it while narrating.
deck() {
  local n=${1:-1}
  raise "$(index_of deck)"
  osa -e 'tell application "System Events" to key code 115' > /dev/null   # Home
  for _ in $(seq 1 $((n - 1))); do
    osa -e 'tell application "System Events" to key code 124' > /dev/null # right arrow
  done
  printf '\033[2m   ...deck on slide %s. cmd-tab back here to continue.\033[0m\n' "$n"
  hold
}

close() { osa -e "tell application \"Google Chrome\" to close window id $(win)" > /dev/null; rm -f "$STATE"; echo closed; }

case "${1:-}" in
  setup) setup ;;
  show)  show "${2:?which tab}" "${3:-}" ;;
  deck)  deck "${2:-1}" ;;
  close) close ;;
  *) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

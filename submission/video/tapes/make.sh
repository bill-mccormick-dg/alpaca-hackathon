#!/usr/bin/env bash
# Render the terminal half straight to video, no screen recorder (#23).
#
# vhs runs each shot for real over ssh and writes a 1920x1080 mp4. Nothing is
# staged: these are the same commands demo.sh runs, on the same host, printing
# the same captions. What changes is that a fluffed take costs nothing, the font
# size is chosen rather than whatever the terminal happened to be at, and the
# frame has no window chrome, no tab bar and no clock in it.
#
#   bash tapes/make.sh                 all four
#   bash tapes/make.sh shot2           just one
#   FORCE=--force bash tapes/make.sh   shot 1 outside market hours (see below)
#   SKIP_HALT=0 bash tapes/make.sh     let shot 4 really flatten the test account
#
# Two shots depend on the world, not on us:
#
#   shot1  is a live cycle. Outside 09:45-15:15 ET it prints "outside trading
#          window" and stops - which is what wrecked the first take. FORCE
#          runs the cycle anyway; it is still real, just after hours. For the
#          final cut, render this one while the market is open.
#
#          It runs against `official`, not demo.sh's default `test`, and the
#          reason is latency, not safety - it is --dry-run either way, so no
#          order is placed on either account. `test` is running the Kimi-K3
#          qualification trial with request_timeout_sec 240, and the first
#          render of this shot was 199 seconds of blank screen waiting on it.
#          `official` answers in about three.
#   shot4  closes the TEST account's real positions and trips a real kill
#          switch. It is skipped by default so re-rendering the other shots is
#          free; set SKIP_HALT=0 for the take.
#
# Requires: brew install vhs ffmpeg, and ssh to the host.
set -euo pipefail
cd "$(dirname "$0")"

HOST=${HOST:-root@192.168.212.10}
REMOTE=${REMOTE:-/opt/alpaca-hackathon}
OUT=${OUT:-../build/shots}
FORCE=${FORCE:-}
SKIP_HALT=${SKIP_HALT:-1}
FONT_SIZE=${FONT_SIZE:-22}

# Knowing when a shot is over. The obvious way - print a sentinel and have the
# tape Wait for it - works for the short shots and hangs on shot 5+7, whose
# output scrolls; and a Wait that times out makes vhs discard the whole
# recording, so the failure mode is losing the take rather than trimming it
# badly. So: record for a fixed generous budget, then find where the picture
# stopped changing and cut there. The remote command hides the cursor when it
# finishes, which is what makes the tail a true freeze and not a blink.
#
# HOLD is how much of that still frame to keep, so a shot does not cut the
# instant its last line lands.
HOLD=0.8

# These commands finish in about a second - last_cycle.py prints a screenful and
# exits - so without pacing every shot is a frozen wall of text under half a
# minute of narration. REVEAL puts the output back on a human clock: one line at
# a time, at roughly the rate the narrator reads down it. Same bytes, same
# commands; only the delay between lines is ours.
#
# name | demo.sh shots | reveal (s/line) | budget (s) | account | what it is
#
# The budget only has to be more than the shot takes; the freeze trim removes
# whatever is left. Overshooting costs render time, not footage.
SHOTS=(
  "shot1|1|0.05|100|official|a live cycle: gates, snapshot, the model, then the risk gate"
  "shot2|2|0.90|60|test|last_cycle.py - what the judged account was shown, and what it did"
  "shot4|4|0.50|90|test|the kill switch, verified against the broker"
  "shot57|5 7|0.35|75|test|the end-of-day digest, then what is actually deployed"
)

mkdir -p "$OUT" generated
command -v vhs >/dev/null || { echo "vhs not found - brew install vhs" >&2; exit 1; }

# Where the picture stops changing for good: the last freeze that never ends.
# Empty if the shot never settles, which is a real answer too - keep it all.
freeze_start() {
  ffmpeg -v info -i "$1" -vf "freezedetect=n=-60dB:d=1.0" -map 0:v -f null - 2>&1 \
    | grep -oE "freeze_(start|end): [0-9.]+" \
    | awk -F'[: ]+' 'END { if ($1 == "freeze_start") print $2 }'
}

render() {
  local name=$1 shots=$2 reveal=$3 budget=$4 acct=$5 note=$6
  local cfg=config-test.yaml
  [ "$acct" = official ] && cfg=config.yaml
  local tape="generated/$name.tape" raw="$OUT/$name.raw.mp4" final="$OUT/$name.mp4"
  # No single quotes anywhere in here: the whole thing is one single-quoted ssh
  # argument, and VHS gets it inside a backtick string so the double quotes and
  # the ssh quotes both survive.
  local pace="while IFS= read -r L; do printf \"%s\\n\" \"\$L\"; sleep $reveal; done"
  # PYTHONUNBUFFERED, because none of this is a terminal any more. Python block-
  # buffers when its stdout is a pipe, and both the pacer and vhs are pipes: the
  # first render of shot 1 sat blank for three minutes and then dumped the whole
  # cycle at the end, which is exactly backwards from what the shot is for.
  # \033[?25l hides the cursor: a blinking one would keep the tail from reading
  # as a freeze, and the freeze is how we know the shot ended.
  local remote="cd $REMOTE && PYTHONUNBUFFERED=1 PAUSE=0 ACCT=$acct CFG=$cfg FORCE=$FORCE SKIP_HALT=$SKIP_HALT bash submission/video/demo.sh $shots | $pace; printf \"\\033[?25l\""

  printf '\033[1;35m== %s\033[0m  %s\n' "$name" "$note"
  [ "$name" = shot1 ] && [ -z "$FORCE" ] && \
    printf '   \033[33mnote\033[0m outside market hours this shot is one line; FORCE=--force to run it anyway\n'
  [ "$name" = shot4 ] && [ "$SKIP_HALT" = 1 ] && \
    printf '   \033[33mnote\033[0m skipping the flatten (SKIP_HALT=1) - this shot will be empty\n'

  # Written, not hand-kept: the tape has to carry the same host and shot numbers
  # the loop above chose, and a second copy of those would be a second thing to
  # forget to update.
  cat > "$tape" <<TAPE
Output $raw
Set Shell "bash"
Set FontSize $FONT_SIZE
Set Width 1920
Set Height 1080
Set Padding 30
Set Margin 0
Set Theme "Catppuccin Mocha"
Set WindowBar "Colorful"
Set TypingSpeed 8ms
Set Framerate 30

# The ssh invocation is scaffolding, not content: demo.sh prints its own caption
# and its own '\$ command' line, and those are what should be on screen.
#
# VHS gets the command as a backtick string, because it contains both the ssh
# single quotes and the pacer's double quotes. The backticks are escaped: this
# heredoc is unquoted (it has to interpolate \$HOST and \$remote), so bare ones
# would run the whole ssh line locally instead of writing it to the tape.
Hide
Type \`clear && ssh -o BatchMode=yes $HOST '$remote'\`
Enter
Sleep 1500ms
Show
Sleep ${budget}s
TAPE

  vhs "$tape"
  local len keep
  len=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$raw" 2>/dev/null || echo 0)
  case "$len" in ''|N/A|0) echo "   FAILED - vhs wrote nothing usable to $raw" >&2; return 1 ;; esac

  keep=$(freeze_start "$raw")
  keep=$(echo "${keep:-$len} $HOLD $len" | awk '{k=$1+$2; print (k<$3 ? k : $3)}')
  # A shot that was frozen from the start never ran - keep the whole thing so
  # there is something to look at while working out why.
  if [ "$(echo "$keep" | awk '{print ($1 < 3)}')" = 1 ]; then
    printf '   \033[33mwarn\033[0m nothing moved in this shot - keeping all %ss\n' "${len%.*}"
    keep=$len
  fi
  ffmpeg -y -v error -i "$raw" -t "$keep" -c copy "$final"
  rm -f "$raw"
  printf '   \033[32mok\033[0m %s  %ss  (of %ss recorded)\n' "$final" "${keep%.*}" "${len%.*}"
}

want=("$@")
[ ${#want[@]} -eq 0 ] && want=(shot1 shot2 shot4 shot57)
for w in "${want[@]}"; do
  found=
  for s in "${SHOTS[@]}"; do
    IFS='|' read -r name shots reveal budget acct note <<<"$s"
    [ "$name" = "$w" ] && { render "$name" "$shots" "$reveal" "$budget" "$acct" "$note"; found=1; }
  done
  [ -n "$found" ] || { echo "no shot '$w' - one of: shot1 shot2 shot4 shot57" >&2; exit 1; }
done

#!/usr/bin/env bash
# Capture the CLI side of one ten-minute cycle, to overlay on the viewer take.
#
#   bash submission/video/cycle_capture.sh 1400          the cycle's console output
#   bash submission/video/cycle_capture.sh 1400 live     ...plus last_cycle.py and
#                                                        status.py, which only mean
#                                                        anything right after the fire
#
# Renders build/cycles/HHMM-<view>.mp4 with vhs (same frame, font and theme as
# tapes/make.sh; the terminal half of the video stays one visual system) and
# writes one row per cycle to cycles.txt: what happened, equity / P&L / positions,
# the journal's own timestamps for that cycle (the sync points against a viewer
# take that started at HH:MM:00), the viewer take and its cut, the renders.
#
# Read-only against the account: the cycle view is a slice of the cron log, and
# the other two are the read-only status tools. Nothing here journals.
set -euo pipefail
cd "$(dirname "$0")"
HOST=${HOST:-root@192.168.212.10}
REMOTE=$(pwd)/cli_remote.sh
OUT=build/cycles; TAPES=build/cycles/tapes; LIST=cycles.txt; TL=timelapse.txt
FONT_SIZE=${FONT_SIZE:-22}; HOLD=0.8
t=${1:?HHMM}; mode=${2:-}
[ "$t" = "${t//[!0-9]/}" ] && [ ${#t} -eq 4 ] || { echo "cycle must be HHMM" >&2; exit 1; }
mkdir -p "$OUT" "$TAPES"
command -v vhs >/dev/null || { echo "vhs not found - brew install vhs" >&2; exit 1; }

freeze_start() {
  ffmpeg -v info -i "$1" -vf "freezedetect=n=-60dB:d=1.0" -map 0:v -f null - 2>&1 \
    | grep -oE "freeze_(start|end): [0-9.]+" \
    | awk -F'[: ]+' 'END { if ($1 == "freeze_start") print $2 }'
}

render() {   # view budget
  local view=$1 budget=$2 tape="$TAPES/$t-$1.tape" raw="$OUT/$t-$1.raw.mp4" final="$OUT/$t-$1.mp4"
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
Hide
Type \`clear && ssh -o BatchMode=yes $HOST bash -s -- $t $view < $REMOTE\`
Enter
Sleep 500ms
Show
Sleep ${budget}s
TAPE
  vhs "$tape" >/dev/null 2>&1
  local len keep
  len=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$raw" 2>/dev/null || echo 0)
  case "$len" in ''|N/A|0) echo "  FAILED $view - vhs wrote nothing" >&2; return 1 ;; esac
  keep=$(freeze_start "$raw")
  keep=$(echo "${keep:-$len} $HOLD $len" | awk '{k=$1+$2; print (k<$3 ? k : $3)}')
  # Frozen from the very start means the whole output landed while vhs was still
  # hidden (an instant command). It is a still, so three seconds of it is plenty.
  [ "$(echo "$keep" | awk '{print ($1 < 3)}')" = 1 ] && keep=$(echo "$len" | awk '{print ($1 < 3 ? $1 : 3)}')
  ffmpeg -y -v error -i "$raw" -t "$keep" -c copy "$final" && rm -f "$raw"
  printf '  %-12s %5.1fs  %s\n' "$view" "$keep" "$final"
}

echo "== cycle $t"
render cycle 20
if [ "$mode" = live ]; then
  render last_cycle 30
  render status 30
fi

# ---- the row in cycles.txt -------------------------------------------------
facts=$(ssh -o BatchMode=yes "$HOST" bash -s -- "$t" facts < "$REMOTE" 2>/dev/null | tail -1)
IFS='|' read -r what eq pnl pos ts_start ts_decide ts_order <<<"$facts"
viewer="-"
if [ -f "footage/viewer-cycle-$t.mov" ]; then
  cut=$(grep "^viewer-cycle-$t.mov |" "$TL" 2>/dev/null | awk -F'|' '{gsub(/ /,"",$2); gsub(/ /,"",$3); print $2"-"$3}')
  viewer="viewer-cycle-$t.mov (${cut:-unscanned})"
fi
cli=$(ls "$OUT"/$t-*.mp4 2>/dev/null | xargs -n1 basename | paste -sd',' - | sed 's/,/, /g')
[ -f "$LIST" ] || cat > "$LIST" <<'HDR'
# cycles.txt - one row per ten-minute cycle, for cherry-picking (cycle_capture.sh).
#
#   cycle | what happened | equity / day P&L / positions | journal ts: start / decide / order | viewer take (cut) | CLI renders in build/cycles/
#
# Times are CT. The viewer take started at cycle:00 exactly, so a journal
# timestamp of HH:MM:07 is 7 s into that take - that is the sync point for an
# overlay. `what happened` is the cron log's own decision/order lines.
HDR
row="$t | ${what:-?} | ${eq:-?} / ${pnl:-?} / ${pos:-?} | ${ts_start:-?} / ${ts_decide:-?} / ${ts_order:--} | $viewer | ${cli:--}"
if grep -q "^$t |" "$LIST"; then
  tmp=$(mktemp); awk -v t="$t" -v r="$row" '$0 ~ "^"t" \\|" {print r; next} {print}' "$LIST" > "$tmp" && cat "$tmp" > "$LIST" && rm -f "$tmp"
else
  echo "$row" >> "$LIST"
fi
echo "  $row"

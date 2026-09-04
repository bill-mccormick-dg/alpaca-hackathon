#!/usr/bin/env bash
# One ten-minute cycle as a single clip: the live journal viewer behind, the
# CLI captures of the same cycle in front, synced to the moment the cycle
# landed in the viewer (issue: the live-viewer block, #248).
#
#   bash submission/video/overlay_cycle.sh 1400              cycle view only
#   bash submission/video/overlay_cycle.sh 1400 cycle last_cycle
#                                                            two CLI views, one after the other
#
# Inputs (all made earlier today by the capture loops):
#   footage/viewer-cycle-HHMM.mov      the viewer, recorded from HH:MM:00
#   build/cycles/HHMM-<view>.mp4       the CLI renders (cycle_capture.sh)
#   cycles.txt                         the cycle's journal timestamps: the
#                                      first ("start") is where the panel
#                                      appears, the last is where the picture
#                                      is allowed to end
# Output: footage/cycle-overlay-HHMM.mp4, 1920x1080, silent (assemble.py puts
# the narration on it; make the cuts.txt row `1.0`, not `self`).
#
# The viewer is 1920x1200 with the browser chrome in the top 120 px; those rows
# are cropped off, exactly as timelapse.py does. The CLI panel slides in at the
# sync offset, scaled to SCALE of the frame, bottom-right, with a soft border,
# and HOLDS its last frame until the next view replaces it or the clip ends. The
# clip runs until the later of: the last panel finishing plus TAIL, or the
# cycle's last journal event plus SETTLE - so the order line always lands on
# screen - and never past the end of the take.
set -euo pipefail
cd "$(dirname "$0")"
t=${1:?HHMM}; shift
views=("$@"); [ ${#views[@]} -eq 0 ] && views=(cycle)
SCALE=${SCALE:-0.58}; MARGIN=${MARGIN:-40}; TAIL=${TAIL:-1.5}; LEAD=${LEAD:-0.4}; SETTLE=${SETTLE:-2.5}
bg="footage/viewer-cycle-$t.mov"; out="footage/cycle-overlay-$t.mp4"
[ -f "$bg" ] || { echo "no viewer take $bg" >&2; exit 1; }
take=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$bg")

# the cycle's journal timestamps, CT, from cycles.txt: "HH:MM:SS / HH:MM:SS / HH:MM:SS|-"
row=$(grep "^$t |" cycles.txt || true)
[ -n "$row" ] || { echo "no $t row in cycles.txt - run cycle_capture.sh $t first" >&2; exit 1; }
stamps=$(echo "$row" | awk -F'|' '{print $4}')
secs() { echo "$1" | awk -F: 'NF==3 {printf "%d", $3+0}'; }          # :SS of HH:MM:SS
first=$(secs "$(echo "$stamps" | awk -F'/' '{gsub(/ /,"",$1); print $1}')")
last=$first
for s in $(echo "$stamps" | tr '/' ' '); do v=$(secs "$s"); [ -n "$v" ] && [ "$v" -gt "$last" ] && last=$v; done
sync=$(awk -v s="$first" -v l="$LEAD" 'BEGIN{v=s-l; if (v<0) v=0; printf "%.2f", v}')

W=1920; H=1080
pw=$(awk -v w=$W -v s=$SCALE 'BEGIN{printf "%d", int(w*s/2)*2}')
ph=$(awk -v h=$H -v s=$SCALE 'BEGIN{printf "%d", int(h*s/2)*2}')
x=$((W - pw - MARGIN)); y=$((H - ph - MARGIN))

# panel start times: each view begins when the previous one's render ends
starts=(); ends=(); at=$sync; inputs=(-i "$bg")
for v in "${views[@]}"; do
  f="build/cycles/$t-$v.mp4"
  [ -f "$f" ] || { echo "no CLI render $f - run cycle_capture.sh $t${v:+ live}" >&2; exit 1; }
  d=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$f")
  inputs+=(-i "$f"); starts+=("$at"); at=$(awk -v a="$at" -v d="$d" 'BEGIN{printf "%.2f", a+d}'); ends+=("$at")
done
total=$(awk -v a="${ends[$((${#ends[@]}-1))]}" -v tl="$TAIL" -v j="$last" -v st="$SETTLE" -v tk="$take" \
        'BEGIN{v=a+tl; w=j+st; if (w>v) v=w; if (v>tk) v=tk; printf "%.2f", v}')

filters="[0:v]scale=$W:-2,crop=$W:min(ih\\,$H):0:ih-min(ih\\,$H),setsar=1,fps=30[bgc];"
prev="[bgc]"
for i in "${!views[@]}"; do
  n=$((i+1)); from=${starts[$i]}
  if [ $((i+1)) -lt ${#views[@]} ]; then to=${starts[$((i+1))]}; else to=$total; fi
  # scale, soft border, hold the last frame (tpad clone), shift onto the take's clock
  filters+="[$n:v]scale=$pw:$ph,setsar=1,pad=iw+6:ih+6:3:3:color=0x89b4fa@0.55,fps=30,tpad=stop_mode=clone:stop_duration=120,setpts=PTS+$from/TB[p$n];"
  filters+="$prev[p$n]overlay=$((x-3)):$((y-3)):enable='between(t,$from,$to)':eof_action=pass[o$n];"
  prev="[o$n]"
done
filters="${filters}${prev}trim=0:$total,setpts=PTS-STARTPTS[v]"

ffmpeg -v error -y "${inputs[@]}" -filter_complex "$filters" -map "[v]" -an \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p -t "$total" "$out"
printf 'cycle %s: %s -> %s  (%.1fs of a %.1fs take; panel at %ss, journal last event at :%02d)\n' \
  "$t" "${views[*]}" "$out" "$total" "$take" "$sync" "$last"

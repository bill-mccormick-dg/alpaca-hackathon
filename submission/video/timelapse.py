#!/usr/bin/env python3
"""Stitch the viewer screen recordings into one time-lapse of cycles arriving.

The operator recorded the journal viewer once per ten-minute cycle: ~20 s each,
most of it the page sitting still, with the cycle's lines scrolling in somewhere
in the middle. Played back to back with the still parts trimmed off, that is a
time-lapse of the trading day - one cycle after another, seconds apart.

Two steps, because the trim points should be reviewed, not trusted:

    timelapse.py scan            find each recording's scroll, write timelapse.txt
    timelapse.py scan --frames   ...and save a frame at each proposed start/end
                                 to build/timelapse-preview/, to eyeball them
    timelapse.py build           read timelapse.txt, cut each row, join them all
                                 into build/timelapse.mp4

`scan` proposes; `timelapse.txt` is yours to edit (a start, an end, a "skip", the
order); `build` does exactly what the file says. Re-running `scan` refuses to
overwrite an edited list unless you pass --force.

How the scroll is found: ffmpeg's freezedetect reports every span where the
picture stops changing (the same filter tapes/make.sh uses to find where a
terminal shot ends). A cycle's lines land one frame at a time, seconds apart,
so what the filter sees is a still page, a few instantaneous thaws, then still
again - the scroll is the span from the first thaw to the start of the final
freeze, and --pad adds a little air either side. A recording that never changes
is written as `skip`. A moving mouse cursor is far below the noise floor, so it
does not count as motion; the first half-second is ignored, because that is the
recorder's own UI leaving the screen.

The recordings are 1920x1200 - 120 px taller than the 1920x1080 output. Those
120 px are the browser chrome at the top, so the default is to crop them off
(--fit crop-top); --fit pad letterboxes instead.

Stdlib only, so ./.venv/bin/python and system python3 both work. Needs ffmpeg
and ffprobe (brew install ffmpeg). Audio is dropped - these were narrated live
in fragments, and a join of those is noise; --keep-audio keeps it anyway.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOOTAGE = HERE / "footage"
BUILD = HERE / "build"
LIST = HERE / "timelapse.txt"
OUT = BUILD / "timelapse.mp4"
PREVIEW = BUILD / "timelapse-preview"
DEFAULT_GLOB = "Screen Recording *.mov"   # what macOS names them

W, H, FPS = 1920, 1080, 30
NOISE_DB, MIN_FREEZE, PAD = -60, 0.3, 0.75
SETTLE = 0.5      # seconds at the start that are the recorder's UI, not the page
GAP = 4.0         # changes further apart than this are separate bursts


def die(msg: str) -> None:
    print(f"timelapse.py: {msg}", file=sys.stderr)
    sys.exit(1)


def need(tool: str) -> None:
    if not shutil.which(tool):
        die(f"{tool} not found - brew install ffmpeg")


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def freezes(path: Path, noise_db: float, min_freeze: float) -> list[tuple[float, float]]:
    """Frozen spans as (start, end), end = clip duration if it never thaws."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path),
         "-vf", f"freezedetect=n={noise_db}dB:d={min_freeze}",
         "-map", "0:v", "-f", "null", "-"],
        capture_output=True, text=True)
    spans: list[tuple[float, float]] = []
    start: float | None = None
    for key, val in re.findall(r"freeze_(start|end): ([0-9.]+)", proc.stderr):
        t = float(val)
        if key == "start":
            start = t
        elif start is not None:
            spans.append((start, t))
            start = None
    if start is not None:
        spans.append((start, duration(path)))
    return spans


def changes(total: float, frozen: list[tuple[float, float]]) -> list[float]:
    """Every moment the picture changed, from the frozen spans.

    A thaw (a freeze's end) is a change. If the next freeze does not start at
    that same instant, the picture kept moving until it did - that end is a
    change too. Motion before the first freeze counts only if it lasts longer
    than SETTLE, otherwise it is the recorder's own UI going away.
    """
    if not frozen:
        return [0.0, total]
    out: list[float] = []
    if frozen[0][0] > SETTLE:
        out += [0.0, frozen[0][0]]
    for i, (s, e) in enumerate(frozen):
        last = i == len(frozen) - 1
        if last and e >= total - 0.1:
            break                      # frozen to the end: nothing after this
        out.append(e)
        nxt = frozen[i + 1][0] if not last else total
        if nxt - e > 0.05:
            out.append(nxt)
    return out


def scroll(ch: list[float], gap: float) -> tuple[list[float], list[float]]:
    """Split the change instants into bursts more than `gap` seconds apart.

    The burst with the most changes (earliest on a tie) is the scroll; the
    rest are strays - a cursor flicker, a notification, the page's own
    housekeeping a few seconds after the cycle - that would otherwise stretch
    the cut by ten seconds of nothing. Returns (scroll, strays).
    """
    if not ch:
        return [], []
    bursts, cur = [], [ch[0]]
    for c in ch[1:]:
        if c - cur[-1] > gap:
            bursts.append(cur)
            cur = [c]
        else:
            cur.append(c)
    bursts.append(cur)
    best = max(bursts, key=lambda b: (len(b), -b[0]))
    strays = [c for b in bursts if b is not best for c in b]
    return best, strays


def recordings(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in patterns or [DEFAULT_GLOB]:
        q = Path(p)
        files += [q] if q.is_file() else sorted(FOOTAGE.glob(p))
    if not files:
        die(f"no recordings match {patterns or [DEFAULT_GLOB]} in {FOOTAGE}")
    # Chronological by when the recording finished, not by name: macOS names
    # them "9.59.51 AM" and "10.10.18 AM", and "9" sorts after "1".
    return sorted(set(files), key=lambda f: f.stat().st_mtime)


def fmt(t: float) -> str:
    return f"{t:.2f}"


# ------------------------------------------------------------------ scan

def scan(args: argparse.Namespace) -> int:
    need("ffmpeg"); need("ffprobe")
    if LIST.exists() and not (args.force or args.append):
        die(f"{LIST.name} exists - edit it, run `build`, pass --append for new recordings, or --force to regenerate")
    files = recordings(args.files)
    if args.frames:
        PREVIEW.mkdir(parents=True, exist_ok=True)

    # --append: keep the list you have edited, add rows only for recordings it
    # does not mention yet. Recordings arrive one cycle at a time all day.
    existing = LIST.read_text().rstrip("\n").splitlines() if (args.append and LIST.exists()) else []
    if existing:
        listed = {l.split("|")[0].strip() for l in existing if l.strip() and not l.startswith("#")}
        files = [f for f in files if f.name not in listed and str(f) not in listed]
        if not files:
            print(f"nothing new - every recording is already in {LIST.name}")
            return 0

    rows = existing or [
        "# timelapse.txt - the viewer recordings, trimmed to the scroll (timelapse.py).",
        "#",
        "#   file | start | end | note",
        "#",
        "# start/end are seconds into that file. `-` means the very beginning / the",
        "# very end. `skip` in the start column drops the row. Rows play in this order.",
        f"# Written by `timelapse.py scan` (noise {args.noise}dB, min freeze {args.min_freeze}s,",
        f"# pad {args.pad}s); the note says what it saw. Edit anything; `build` obeys the file.",
        "",
    ]
    total_kept = 0.0
    for f in files:
        d = duration(f)
        ch = changes(d, freezes(f, args.noise, args.min_freeze))
        if not ch:
            rows.append(f"{f.name} | skip | - | never changes ({d:.1f}s)")
            print(f"  skip   {f.name}  (nothing moves in {d:.1f}s)")
            continue
        keep, strays = scroll(ch, args.gap)
        start = max(0.0, keep[0] - args.pad)
        end = min(d, keep[-1] + args.pad)
        note = f"changes at {', '.join(f'{c:.1f}' for c in keep)} of {d:.1f}s"
        if strays:
            note += f"; ignored stray change(s) at {', '.join(f'{c:.1f}' for c in strays)}"
        if keep[-1] - keep[0] > 8.0:
            note += " - long span; check it is one scroll, not a stray move"
        rows.append(f"{f.name} | {fmt(start)} | {fmt(end)} | {note}")
        total_kept += end - start
        print(f"  {fmt(start):>6} - {fmt(end):<6} {f.name}  ({note})")
        if args.frames:
            stem = re.sub(r"[^A-Za-z0-9]+", "-", f.stem).strip("-")
            for tag, t in (("start", start), ("end", max(start, end - 0.05))):
                # Output-side seek, for the same reason as in build(): the frame
                # on screen at t is usually one emitted well before t.
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-y", "-i", str(f), "-ss", fmt(t),
                     "-frames:v", "1", str(PREVIEW / f"{stem}-{tag}.png")], check=True)
    LIST.write_text("\n".join(rows) + "\n")
    print(f"\n{len(files)} recording(s), {total_kept:.1f}s kept -> {LIST}")
    if args.frames:
        print(f"start/end frames in {PREVIEW}")
    print("Review the list, then: timelapse.py build")
    return 0


# ------------------------------------------------------------------ build

def read_list() -> list[tuple[Path, float | None, float | None]]:
    if not LIST.exists():
        die(f"{LIST.name} missing - run `timelapse.py scan` first")
    rows = []
    for line in LIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) != 4:
            die(f"{LIST.name}: expected 4 fields, got {len(f)}: {line}")
        name, start, end, _ = f
        if start == "skip":
            continue
        path = Path(name) if Path(name).is_absolute() else FOOTAGE / name
        if not path.exists():
            die(f"{LIST.name}: no such recording {path}")
        num = lambda s: None if s == "-" else float(s)
        rows.append((path, num(start), num(end)))
    if not rows:
        die(f"{LIST.name}: every row is skipped")
    return rows


def build(args: argparse.Namespace) -> int:
    need("ffmpeg"); need("ffprobe")
    rows = read_list()
    seg_dir = BUILD / "timelapse-segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    for old in seg_dir.glob("*.mp4"):
        old.unlink()

    # Every segment is re-encoded to one geometry and frame rate so the concat
    # is a plain stream copy - the recordings can differ in size or rate, and a
    # concat of mismatched streams is the classic silent-failure.
    if args.fit == "crop-top":
        # Scale to the output width, then keep the BOTTOM H rows: the excess
        # height is the browser's tab and address bars, and they are at the top.
        fit = f"scale={W}:-2,crop={W}:min(ih\\,{H}):0:ih-min(ih\\,{H}),pad={W}:{H}:0:0:color=black"
    else:
        fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black")
    vf = f"{fit},setpts=PTS/{args.speed},fps={FPS},format=yuv420p"
    parts, total = [], 0.0
    for i, (path, start, end) in enumerate(rows, 1):
        seg = seg_dir / f"{i:02d}.mp4"
        # Seek on the OUTPUT side and cut by duration. These recordings are
        # variable-frame-rate - the recorder emits a frame only when the screen
        # changes - so an input-side seek lands on whatever sparse frame is
        # nearest and drops the still frame that should be on screen at the cut.
        # Decoding from the start costs nothing at twenty seconds a file.
        s0 = start or 0.0
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ss", fmt(s0)]
        if end is not None:
            cmd += ["-t", fmt(max(0.0, end - s0))]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
        if args.keep_audio:
            cmd += ["-af", f"atempo={min(max(args.speed, 0.5), 2.0)}", "-c:a", "aac"]
        else:
            cmd += ["-an"]
        cmd.append(str(seg))
        subprocess.run(cmd, check=True)
        length = duration(seg)
        total += length
        parts.append(seg)
        print(f"  {i:02d}  {length:5.1f}s  {path.name}  [{start if start is not None else 0:.2f} -> {end if end is not None else 'end'}]")

    concat = seg_dir / "concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    out = Path(args.out) if args.out else OUT
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-c", "copy", str(out)], check=True)
    m, s = divmod(round(total), 60)
    print(f"\n{len(parts)} segment(s), {m}:{s:02d} -> {out}")
    if args.open:
        subprocess.run(["open", str(out)])
    return 0


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="propose start/end for each recording -> timelapse.txt")
    s.add_argument("files", nargs="*", help=f"recordings or globs under footage/ (default: '{DEFAULT_GLOB}')")
    s.add_argument("--pad", type=float, default=PAD, help=f"seconds of air before/after the scroll (default {PAD})")
    s.add_argument("--noise", type=float, default=NOISE_DB, help=f"freezedetect noise floor in dB (default {NOISE_DB})")
    s.add_argument("--min-freeze", type=float, default=MIN_FREEZE, help=f"seconds still before it counts as frozen (default {MIN_FREEZE})")
    s.add_argument("--gap", type=float, default=GAP, help=f"changes more than this many seconds apart are separate bursts; only the biggest burst is kept (default {GAP})")
    s.add_argument("--frames", action="store_true", help="save a frame at each proposed start and end for review")
    s.add_argument("--force", action="store_true", help="overwrite an existing timelapse.txt")
    s.add_argument("--append", action="store_true", help="keep the existing list; add rows only for recordings not in it yet")
    s.set_defaults(fn=scan)

    b = sub.add_parser("build", help="cut and join per timelapse.txt -> build/timelapse.mp4")
    b.add_argument("--speed", type=float, default=1.0, help="playback multiplier inside each cut (2.0 = twice as fast)")
    b.add_argument("--fit", choices=("crop-top", "pad"), default="crop-top",
                   help="how a taller-than-16:9 recording becomes 1920x1080: crop the browser chrome off the top (default) or letterbox")
    b.add_argument("--keep-audio", action="store_true", help="keep the recordings' audio (dropped by default)")
    b.add_argument("--out", default=None, help=f"output file (default {OUT.relative_to(HERE)})")
    b.add_argument("--open", action="store_true", help="open the result when done")
    b.set_defaults(fn=build)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

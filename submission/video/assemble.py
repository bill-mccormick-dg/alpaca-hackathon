#!/usr/bin/env python3
"""Cut the submission video from parts, to fit narration recorded separately (#23).

The one-take approach (`record.sh`) needs the market open, a clean screen and no
fluffed lines all at once, and it did not survive contact. This does the same job
out of pieces that can each be redone alone:

    slides   rendered from submission/slides.pdf     - always available
    shots    rendered by vhs from the real commands  - tapes/make.sh
    clips    the screen recordings we already have   - footage/
    voice    recorded per narration block, any format ffmpeg reads

Picture is cut to fit the voice, never the other way round: every row in
cuts.txt takes its length from its narration block's audio. So fixing a fluffed
line is re-recording one 20-second file and running this again - which matters
because slide 18 (Results) cannot be filled in until Thursday's close, and this
will be re-rendered on Friday morning regardless.

    assemble.py --check          what exists, what is missing, how long it runs
    assemble.py --scratch        build with macOS `say` as a stand-in voice
    assemble.py                  build with whatever is in narration/
    assemble.py --open           ...and open the result

Stdlib only, so ./.venv/bin/python and system python3 both work. Needs ffmpeg
and pdftoppm (brew install ffmpeg poppler).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLIDES_PDF = HERE.parent / "slides.pdf"
SLIDES_MD = HERE.parent / "SLIDES.md"
NARRATION_MD = HERE / "narration.md"
CUTS = HERE / "cuts.txt"
FOOTAGE = HERE / "footage"
VOICE = HERE / "narration"
BUILD = HERE / "build"
OUT = BUILD / "autobelay.mp4"

W, H, FPS = 1920, 1080, 30
CAP_SECONDS = 300.0          # the hackathon's hard cap
CAP_BYTES = 300 * 1024 * 1024
AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".aiff", ".aif", ".caf", ".flac")
# Whatever the screen recorder produced. QuickTime writes .mov, the Home
# Assistant and viewer retakes arrived as .mp4; cuts.txt should not have to care.
CLIP_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
# How far a clip may be sped up or slowed down to meet its target before we stop
# stretching and freeze the last frame instead. Beyond 2x, screen recordings
# stop reading as footage and start reading as a mistake.
SPEED_MIN, SPEED_MAX = 0.5, 2.0

# Every take starts with the reach for the record button and ends with the reach
# back. Across nine blocks that was eleven seconds - more than the amount this
# cut is over by - and it is also why the joins sounded slack. So each block's
# lead-in and run-out are normalised to a fixed breath rather than left as
# whatever the hand did. Words are never trimmed: the silence threshold is well
# below speech, and what is kept is padded back out.
LEAD_PAD, TAIL_PAD = 0.15, 0.30
SILENCE_DB, SILENCE_MIN = -38, 0.25

# Rendering every slide at 144dpi turns slides.pdf's 960x540pt page into exactly
# 1920x1080 - no resampling, so the deck's text stays as sharp as the PDF is.
SLIDE_DPI = 144

# A part that has not been captured yet becomes a flat card rather than an error,
# so the whole cut stays watchable while pieces are still missing - which is the
# point of building it out of parts. Every one of them is named in red in the
# report and again at the end, so a blank card is never a silent one. (This
# ffmpeg has no drawtext filter, hence a colour rather than a caption.)
MISSING = "#1e1e2e"

VF_FIT = (
    f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
)


def die(msg: str) -> None:
    print(f"\033[31merror\033[0m {msg}", file=sys.stderr)
    raise SystemExit(1)


def run(cmd: list[str], quiet: bool = True) -> None:
    r = subprocess.run(cmd, capture_output=quiet, text=True)
    if r.returncode != 0:
        tail = (r.stderr or "")[-2000:] if quiet else ""
        die(f"{cmd[0]} failed: {' '.join(cmd[1:6])}...\n{tail}")


def duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        die(f"cannot probe {path}")
    return float(json.loads(r.stdout)["format"]["duration"])


# ---------------------------------------------------------------- narration


@dataclass
class Block:
    id: str
    label: str
    secs: float          # the scripted length, used when no audio exists yet
    text: str
    _span: tuple[float, float] | None = None

    def audio(self) -> Path | None:
        for ext in AUDIO_EXTS:
            p = VOICE / f"{self.id}{ext}"
            if p.exists():
                return p
        p = BUILD / "scratch" / f"{self.id}.aiff"
        return p if p.exists() else None

    def span(self) -> tuple[float, float]:
        """(start, length) of this block's audio, dead air at the ends removed."""
        if self._span is None:
            a = self.audio()
            self._span = voiced_span(a) if a else (0.0, self.secs)
        return self._span


def voiced_span(path: Path) -> tuple[float, float]:
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-vn", "-af",
         f"silencedetect=n={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    total = duration(path)
    marks = re.findall(r"silence_(start|end): (-?[0-9.]+)", r.stderr or "")
    head = 0.0
    for kind, at in marks:                      # a silence that begins at 0 is lead-in
        if kind == "start" and float(at) < 0.05:
            nxt = marks[marks.index((kind, at)) + 1:]
            if nxt and nxt[0][0] == "end":
                head = float(nxt[0][1])
        break
    tail = total
    if marks and marks[-1][0] == "start":       # a silence never closed is run-out
        tail = float(marks[-1][1])
    start = max(0.0, head - LEAD_PAD)
    end = min(total, tail + TAIL_PAD)
    return (start, max(0.5, end - start))


# **[1:20 — shot 2, `last_cycle.py` — 45s]**  ...and the body until the next one.
HEAD = re.compile(r"^\*\*\[(\d+):(\d+)\s+[—-]\s+(.+?)\s+[—-]\s+(\d+)s\]\*\*", re.M)
DIRECTOR = re.compile(r"\*\(director:.*?\)\*", re.S)


def narration_blocks() -> list[Block]:
    """narration.md is the script humans edit; this reads it rather than a copy.

    A second list of the same lines is a list that goes stale - the deck outline
    already drifted three slides that way (SLIDES.md), so the running order and
    the words both come from the file the writing happens in.
    """
    md = NARRATION_MD.read_text()
    md = md.split("## If it runs long")[0]
    heads = list(HEAD.finditer(md))
    if not heads:
        die(f"no narration blocks found in {NARRATION_MD}")
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        body = md[m.end():end]
        body = DIRECTOR.sub("", body)
        body = body.split("\n---")[0]
        # Markdown emphasis and code ticks are for the reader, not the speaker.
        body = re.sub(r"[*`_]", "", body).strip()
        body = re.sub(r"\s+", " ", body)
        out.append(Block(f"{i + 1:02d}", m.group(3), float(m.group(4)), body))
    return out


def make_scratch(blocks: list[Block]) -> None:
    """A stand-in voice, so the cut is watchable before anyone records anything.

    `say` is not the take - it is a metronome. It gives every block a real length
    so the pacing, the splices and the 5:00 cap can be judged today, and the real
    recording drops straight into narration/ over the top of it.
    """
    if not shutil.which("say"):
        die("--scratch needs macOS `say`")
    d = BUILD / "scratch"
    d.mkdir(parents=True, exist_ok=True)
    for b in blocks:
        target = d / f"{b.id}.aiff"
        if any((VOICE / f"{b.id}{e}").exists() for e in AUDIO_EXTS):
            continue        # a real take exists; never speak over it
        # 180 wpm is a brisk-but-natural read; the scripted seconds assume it.
        run(["say", "-r", "180", "-o", str(target), b.text])
        print(f"  scratch {b.id}  {duration(target):5.1f}s  {b.label}")


# --------------------------------------------------------------------- cuts


@dataclass
class Row:
    n: str
    kind: str
    source: str
    tin: float | None
    tout: float | None
    narr: str
    share: float
    note: str
    secs: float = 0.0       # filled in once the narration is known
    offset: float = 0.0     # where in a shared block this row's audio starts

    @property
    def self_voiced(self) -> bool:
        """The clip was narrated live and carries its own sound.

        Its audio and its picture are one recording, so this row is never
        speed-fitted, trimmed to a separate voice file, or frozen: doing any of
        those to a talking screen recording puts the words out of sync with what
        they are describing. It plays at native speed and sets its own length.
        """
        return self.narr == "self"


def read_cuts() -> list[Row]:
    rows = []
    for line in CUTS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) != 8:
            die(f"cuts.txt: expected 8 fields, got {len(f)}: {line}")
        num = lambda s: None if s == "-" else float(s)  # noqa: E731
        rows.append(Row(f[0], f[1], f[2], num(f[3]), num(f[4]), f[5],
                        float(f[6]), f[7]))
    return rows


def plan(rows: list[Row], blocks: dict[str, Block]) -> None:
    """Give every row a length, and every shared block a split."""
    for r in rows:
        if r.self_voiced:
            src = source_of(r)
            if not src.exists():
                die(f"row {r.n} is narrated in its own clip, but {src} is missing")
            if r.tin is not None and r.tout is not None:
                r.offset, r.secs = r.tin, r.tout - r.tin
            else:
                r.offset, r.secs = voiced_span(src)
            continue
        b = blocks.get(r.narr)
        if b is None:
            die(f"cuts.txt row {r.n} names narration block {r.narr}, which does not exist")
        r.secs = b.span()[1] * r.share
    seen: dict[str, float] = {}
    for r in rows:
        if r.self_voiced:
            continue
        r.offset = seen.get(r.narr, 0.0)
        seen[r.narr] = r.offset + r.secs
    for nid in seen:
        shares = sum(r.share for r in rows if r.narr == nid)
        if abs(shares - 1.0) > 0.01:
            die(f"narration block {nid}: shares sum to {shares}, not 1.0")


# ------------------------------------------------------------------ segments


SLIDE_TITLE = re.compile(r"^(\d+)\. \*\*(.+?)\*\*", re.M)


def slide_number(key: str) -> int:
    """Find the one slide whose title contains `key`.

    cuts.txt names slides by title because numbers move: #244 inserted a slide
    at 8 and pushed Results from 18 to 19 mid-branch. SLIDES.md is regenerated
    from the deck, so it is the index to ask - and a key that matches none, or
    more than one, stops the build instead of quietly cutting to the wrong
    picture.
    """
    titles = SLIDE_TITLE.findall(SLIDES_MD.read_text())
    if not titles:
        die(f"no slide titles found in {SLIDES_MD}")
    hits = [(int(n), s) for n, s in titles if key.lower() in s.lower()]
    if len(hits) == 1:
        return hits[0][0]
    if not hits:
        die(f"cuts.txt names slide '{key}', which matches no title in SLIDES.md")
    named = ", ".join(f"{n} ({s})" for n, s in hits)
    die(f"cuts.txt names slide '{key}', which is ambiguous - matches {named}")
    raise AssertionError  # unreachable; die() exits


def render_slides(pages: set[int]) -> dict[int, Path]:
    """Render each page once, cached on the deck's *contents*.

    Not on its mtime: `git checkout` and `git stash pop` rewrite mtimes, and a
    deck that arrives that way looks older than the pictures made from an
    entirely different version of it. That is not hypothetical - it served a
    stale page 19 from the 19-slide deck after #244 made it 20, and the wrong
    slide went into the cut without a word. A content hash cannot do that.
    """
    d = BUILD / "slides"
    d.mkdir(parents=True, exist_ok=True)
    tag = hashlib.md5(SLIDES_PDF.read_bytes()).hexdigest()[:8]
    for old in d.glob("slide-*.png"):
        if tag not in old.name:
            old.unlink()        # a page from a deck we no longer have
    out = {}
    for p in sorted(pages):
        png = d / f"slide-{p:02d}-{tag}.png"
        if not png.exists():
            run(["pdftoppm", "-png", "-r", str(SLIDE_DPI), "-f", str(p), "-l", str(p),
                 "-singlefile", str(SLIDES_PDF), str(png.with_suffix(""))])
        out[p] = png
    return out


def audio_args(row: Row, blocks: dict[str, Block]) -> tuple[list[str], str]:
    """Input args and the filter label for this row's slice of its block."""
    if row.self_voiced:
        return ([], "0:a")
    b = blocks[row.narr]
    a = b.audio()
    if a is None:
        return (["-f", "lavfi", "-t", f"{row.secs:.3f}",
                 "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"], "1:a")
    return (["-ss", f"{b.span()[0] + row.offset:.3f}", "-t", f"{row.secs:.3f}",
             "-i", str(a)], "1:a")


def build_segment(row: Row, blocks: dict[str, Block], slides: dict[str, Path]) -> Path:
    seg = BUILD / "segments" / f"{row.n}.mp4"
    seg.parent.mkdir(parents=True, exist_ok=True)
    T = row.secs
    ain, alabel = audio_args(row, blocks)

    if row.kind == "slide":
        vin = ["-loop", "1", "-t", f"{T:.3f}", "-i", str(slides[row.source])]
        vf = f"{VF_FIT},fps={FPS},format=yuv420p"
    else:
        src = source_of(row)
        if not src.exists():
            run(["ffmpeg", "-y", "-v", "error",
                 "-f", "lavfi", "-t", f"{T:.3f}",
                 "-i", f"color=c={MISSING}:s={W}x{H}:r={FPS}", *ain,
                 "-map", "0:v", "-map", alabel, "-t", f"{T:.3f}",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                 "-movflags", "+faststart", str(seg)])
            return seg
        if row.self_voiced:
            run(["ffmpeg", "-y", "-v", "error",
                 "-ss", f"{row.offset:.3f}", "-t", f"{T:.3f}", "-i", str(src),
                 "-filter:v", f"{VF_FIT},fps={FPS},format=yuv420p",
                 "-map", "0:v", "-map", "0:a", "-t", f"{T:.3f}",
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                 "-movflags", "+faststart", str(seg)])
            return seg
        tin = row.tin or 0.0
        tout = row.tout if row.tout is not None else duration(src)
        L = max(0.05, tout - tin)
        # ratio > 1 slows the clip down, < 1 speeds it up.
        ratio = max(SPEED_MIN, min(SPEED_MAX, T / L))
        if T / L < SPEED_MIN:          # far too much footage: take the front of it
            L = T / SPEED_MIN
            tout = tin + L
            ratio = SPEED_MIN
        vin = ["-ss", f"{tin:.3f}", "-to", f"{tout:.3f}", "-i", str(src)]
        # ...and if it is still short, hold the last frame rather than cut early.
        vf = (f"setpts={ratio:.6f}*PTS,{VF_FIT},fps={FPS},"
              f"tpad=stop_mode=clone:stop_duration={max(0.0, T - L * ratio) + 0.5:.3f},"
              f"format=yuv420p")

    run(["ffmpeg", "-y", "-v", "error", *vin, *ain,
         "-filter:v", vf, "-map", "0:v", "-map", alabel,
         "-t", f"{T:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart", str(seg)])
    return seg


def concat(segs: list[Path]) -> None:
    # Beside the segments, not above them: the concat demuxer resolves each
    # `file` against the list's own directory, not the working directory.
    listing = BUILD / "segments" / "order.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segs))
    joined = BUILD / "joined.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(joined)])
    # One loudness pass over the whole thing, so blocks recorded on different
    # days at different mic distances do not step up and down between cuts.
    run(["ffmpeg", "-y", "-v", "error", "-i", str(joined),
         "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(OUT)])


# --------------------------------------------------------------------- main


def source_of(row: Row) -> Path | None:
    """Where this row's picture comes from, or None if it is a still.

    For a clip, the first extension that exists - and failing that the .mp4
    name, so the "no footage" message names something you can go and create.
    """
    if row.kind == "clip":
        for e in CLIP_EXTS:
            p = FOOTAGE / f"{row.source}{e}"
            if p.exists():
                return p
        return FOOTAGE / f"{row.source}{CLIP_EXTS[0]}"
    if row.kind == "shot":
        return BUILD / "shots" / f"{row.source}.mp4"
    return None


def report(rows: list[Row], blocks: dict[str, Block]) -> float:
    print(f"\n\033[1m{'#':>2}  {'kind':5} {'source':10} {'len':>6}  {'voice':7} what\033[0m")
    total = 0.0
    for r in rows:
        if r.self_voiced:
            voice = "\033[32min-clip\033[0m"
            print(f"{r.n}  {r.kind:5} {r.source:11} {r.secs:5.1f}s  {voice}  {r.note}")
            total += r.secs
            continue
        b = blocks[r.narr]
        a = b.audio()
        if a is None:
            voice = "\033[33mscript\033[0m "
        elif a.parent.name == "scratch":
            voice = "\033[33mscratch\033[0m"
        else:
            voice = "\033[32mrecorded\033[0m"
        src = source_of(r)
        missing = ""
        if src is not None and not src.exists():
            how = "tapes/make.sh" if r.kind == "shot" else f"record it into {FOOTAGE.name}/"
            missing = f"  \033[31m<- blank card: no footage, {how}\033[0m"
        shown = r.source
        if r.kind == "slide":
            shown = f"{r.source[:11]}"
        print(f"{r.n}  {r.kind:5} {shown:11} {r.secs:5.1f}s  {voice} {r.note}{missing}")
        total += r.secs
    m, s = divmod(total, 60)
    over = "  \033[31mOVER THE 5:00 CAP\033[0m" if total > CAP_SECONDS else ""
    print(f"\n    total {int(m)}:{s:04.1f}  ({total:.1f}s of {CAP_SECONDS:.0f}){over}")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="report only, build nothing")
    ap.add_argument("--scratch", action="store_true",
                    help="fill missing blocks with a macOS `say` stand-in voice")
    ap.add_argument("--open", action="store_true", help="open the result when done")
    ap.add_argument("--only", help="build just these rows, comma separated")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe", "pdftoppm"):
        if not shutil.which(tool):
            die(f"{tool} not on PATH (brew install ffmpeg poppler)")
    BUILD.mkdir(exist_ok=True)
    VOICE.mkdir(exist_ok=True)

    blist = narration_blocks()
    if args.scratch:
        print("\033[1mstand-in voice\033[0m")
        make_scratch(blist)
    blocks = {b.id: b for b in blist}
    rows = read_cuts()
    plan(rows, blocks)
    total = report(rows, blocks)

    if args.check:
        return
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        rows = [r for r in rows if r.n in wanted]

    slides = {r.source: slide_number(r.source) for r in rows if r.kind == "slide"}
    pngs = render_slides(set(slides.values()))
    print("\n\033[1mbuilding\033[0m")
    segs = []
    for r in rows:
        print(f"  {r.n} {r.note}")
        segs.append(build_segment(r, blocks, {k: pngs[v] for k, v in slides.items()}))
    if args.only:
        print(f"\nsegments only ({args.only}); drop --only for the full cut")
        return

    concat(segs)
    size = OUT.stat().st_size
    m, s = divmod(duration(OUT), 60)
    bad = []
    if duration(OUT) > CAP_SECONDS:
        bad.append("over 5:00")
    if size > CAP_BYTES:
        bad.append("over 300 MB")
    verdict = "\033[31m" + ", ".join(bad) + "\033[0m" if bad else "\033[32mwithin caps\033[0m"
    print(f"\n{OUT}\n  {int(m)}:{s:04.1f}   {size / 1024 / 1024:.1f} MB   {verdict}")
    print(f"  (scripted total was {total:.1f}s)")
    gaps = [r for r in rows if (p := source_of(r)) is not None and not p.exists()]
    if gaps:
        print("\n\033[33mstill blank\033[0m - these rows are a flat card, not footage:")
        for r in gaps:
            print(f"  {r.n} {r.kind:5} {r.source:8} {r.note}")
    if any(not r.self_voiced and blocks[r.narr].audio() and
           blocks[r.narr].audio().parent.name == "scratch" for r in rows):
        print("\n\033[33mstand-in voice\033[0m - some blocks are macOS `say`, not a recording")
    if args.open:
        subprocess.run(["open", str(OUT)])


if __name__ == "__main__":
    main()

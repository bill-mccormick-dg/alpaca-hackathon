#!/usr/bin/env python3
"""The Results slide's equity curve, generated from logs/equity.jsonl.

`submission/images/equity-curve.png` was a placeholder, and the deck's own
notes said telling a placeholder from a real capture was on the reader. It is
the one number judges will look for hardest, so it should come out of the
journal rather than out of a drawing program.

Plots **percent change from each account's open on the first competition day**,
not raw dollars. The three accounts did not start from the same equity - the
`mixed` variant had been running before the window opened - and a shared
dollar axis would make that head start look like performance. Percent from
each account's own open is the comparison the A/B is actually asking about;
the judged account's dollars go in the caption, where they cannot be misread
as a ranking.

SVG, not PNG: it is sharp at any size in the exported PDF, needs no
rasterisation step, and the deck's `section img` rules treat it identically.

    python scripts/equity_curve.py                    # -> submission/images/
    python scripts/equity_curve.py --start 2026-08-31 --out /tmp/curve.svg
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from render_diagrams import BG, BORDER, DIM, FG, FONT, MONO, esc

EQUITY_LOG = ROOT / "logs/equity.jsonl"
OUT = ROOT / "submission/images/equity-curve.svg"
START = "2026-08-31"        # Monday, the first scored session
LABELS = {"official": "official (judged)", "test": "test (challenger)", "mixed": "mixed (instrument A/B)"}

# Categorical, fixed order, validated for dark mode against this surface with
# the dataviz skill's checker (lightness band, chroma, CVD separation, normal-
# vision separation, contrast - all pass at surface #0f1720). Do not brighten
# these to match the diagrams' palette: the lighter steps fail CVD separation
# between the teal and the purple.
SERIES = [("official", "#31a58e"), ("test", "#8a6ff0"), ("mixed", "#bd7c2a")]

W, H = 1280, 560
L, R, T, B = 122, 1160, 76, 452


def load(path: Path, start: str) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {}
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(r.get("date", "")) >= start and r.get("equity_close") is not None:
            rows.setdefault(r["account"], []).append(r)
    for entries in rows.values():
        entries.sort(key=lambda r: r["date"])
    return rows


def series(rows: list[dict], days: list[str]) -> tuple[list[tuple[int, float]], float]:
    """[(x index, pct from the first open)], and the closing dollar equity.

    Index 0 is the first session's OPEN and index i+1 is the close of days[i],
    so a day is a segment rather than a point. Sharing one x between the
    baseline and day one's close drew the first session as a vertical line at
    the left edge - the move happened, but with nowhere to happen across."""
    base = rows[0].get("equity_open") or rows[0]["equity_close"]
    points = [(0, 0.0)]
    points += [(days.index(r["date"]) + 1, (r["equity_close"] / base - 1) * 100) for r in rows]
    return points, rows[-1]["equity_close"]


def nice_ticks(lo: float, hi: float) -> list[float]:
    span = max(hi - lo, 0.5)
    step = next(s for s in (0.25, 0.5, 1, 2, 2.5, 5, 10, 20) if span / s <= 6)
    first = (int(lo / step) - 1) * step
    return [first + i * step for i in range(int(span / step) + 4) if lo - step <= first + i * step <= hi + step]


def render(data: dict[str, list[dict]], days: list[str]) -> str:
    plotted = [(a, c, *series(data[a], days)) for a, c in SERIES if data.get(a)]
    if not plotted:
        raise SystemExit("no equity rows in the window - nothing to plot")

    values = [p for _, _, pts, _ in plotted for _, p in pts]
    ticks = nice_ticks(min(values), max(values))
    lo, hi = min(ticks), max(ticks)

    slots = len(days) + 1          # the open, then one close per session

    def x_of(i: int) -> float:
        return L if slots == 1 else L + (R - L) * i / (slots - 1)

    def y_of(pct: float) -> float:
        return B - (B - T) * (pct - lo) / (hi - lo or 1)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">',
         (f'<style>.t{{font-family:{FONT};fill:{FG}}}.d{{font-family:{FONT};fill:{DIM}}}'
          f'.m{{font-family:{MONO};fill:{DIM}}}</style>'),
         f'<rect width="{W}" height="{H}" fill="{BG}"/>']

    # grid + y axis
    for t in ticks:
        y = y_of(t)
        zero = abs(t) < 1e-9
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{R}" y2="{y:.1f}" stroke="{FG if zero else BORDER}" '
                 f'stroke-width="{1.5 if zero else 1}" stroke-opacity="{0.45 if zero else 0.5}"'
                 f'{" stroke-dasharray=\'5 4\'" if zero else ""}/>')
        s.append(f'<text class="m" x="{L - 16}" y="{y + 5:.1f}" font-size="16" '
                 f'text-anchor="end">{t:+.2f}%</text>')

    # x axis: the open, then each session's close
    s.append(f'<text class="d" x="{x_of(0):.1f}" y="{B + 34}" font-size="17" text-anchor="middle">'
             f'{date.fromisoformat(days[0]):%a %-d %b} open</text>')
    for i, day in enumerate(days):
        s.append(f'<text class="d" x="{x_of(i + 1):.1f}" y="{B + 34}" font-size="17" '
                 f'text-anchor="middle">{date.fromisoformat(day):%a %-d %b} close</text>')

    # series
    for account, colour, points, close in plotted:
        pts = [(x_of(i), y_of(p)) for i, p in points]
        if not pts:
            continue
        s.append(f'<path d="M{" L".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" '
                 f'stroke="{colour}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
        for x, y in pts:
            # a 2px surface ring so crossing series stay separable where they overlap
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{colour}" stroke="{BG}" stroke-width="2"/>')
        ex, ey = pts[-1]
        final = points[-1][1]
        s.append(f'<text class="t" x="{ex + 16:.1f}" y="{ey + 6:.1f}" font-size="19" font-weight="700" '
                 f'fill="{colour}">{final:+.2f}%</text>')

    # legend - identity is never colour alone; every series is also end-labelled
    lx = L + 8
    for account, colour, points, close in plotted:
        s.append(f'<rect x="{lx}" y="{T - 44}" width="26" height="4" rx="2" fill="{colour}"/>')
        s.append(f'<circle cx="{lx + 13}" cy="{T - 42}" r="5.5" fill="{colour}" stroke="{BG}" stroke-width="2"/>')
        text = f"{LABELS.get(account, account)}  ${close:,.0f}"
        s.append(f'<text class="d" x="{lx + 36}" y="{T - 36}" font-size="17">{esc(text)}</text>')
        lx += 40 + int(len(text) * 8.6)

    s.append(f'<text class="d" x="{L - 4}" y="{H - 22}" font-size="16">'
             f'percent change from each account\'s open on {date.fromisoformat(days[0]):%-d %B}'
             f' - dollar equity at the close in the key</text>')
    s.append("</svg>")
    return "\n".join(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, default=EQUITY_LOG)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--start", default=START, help="first scored session (YYYY-MM-DD)")
    args = ap.parse_args()

    if not args.log.exists():
        print(f"{args.log} not found - copy it from CT 108 first:\n"
              f"  scp root@<host>:/opt/alpaca-hackathon/logs/equity.jsonl logs/", file=sys.stderr)
        return 1

    data = load(args.log, args.start)
    days = sorted({r["date"] for rows in data.values() for r in rows})
    if not days:
        print(f"no equity rows on or after {args.start}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(data, days))
    covered = ", ".join(f"{a} {(d[-1]['equity_close']):,.0f}" for a, d in sorted(data.items()))
    print(f"{args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}: "
          f"{len(days)} session(s) from {days[0]} - {covered}")
    if date.fromisoformat(days[-1]) < date(2026, 9, 3):
        print("  (partial week - regenerate after Thursday's close)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

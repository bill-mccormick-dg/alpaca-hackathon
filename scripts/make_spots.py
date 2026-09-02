#!/usr/bin/env python3
"""Spot illustrations for the slide deck - one per idea that a picture states
faster than a sentence.

These are not decoration. Each one carries the fact the slide is about: the
payoff diagram *is* the defined-risk argument, the strike grid *is* what
changed about the menu, the Brier scale *is* the claim about the prior. Where
a slide already has a diagram or a real chart, it gets no spot.

Referenced from the deck with <img>, deliberately, not inlined: an SVG's
internal <style> is not scoped, and inlining these would leak their classes
into the deck the way the architecture diagrams once did. An <img> is its own
document.

Transparent ground - the deck draws the card behind them.

    python scripts/make_spots.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from render_diagrams import (
    ACCENT,
    BORDER,
    DIM,
    EXT,
    FG,
    FONT,
    GATE,
    MODEL,
    MONO,
    STOP,
    esc,
)

OUT = ROOT / "submission/images"
W, H = 640, 400


def head(w=W, h=H, label="") -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" \
role="img" aria-label="{esc(label)}">
<style>
  .sp-t {{ font-family: {FONT}; fill: {FG}; font-size: 19px; font-weight: 600; }}
  .sp-d {{ font-family: {FONT}; fill: {DIM}; font-size: 17px; }}
  .sp-m {{ font-family: {MONO}; fill: {DIM}; font-size: 16px; }}
</style>
'''


def axes(x0, y0, x1, y1) -> str:
    return (f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="{BORDER}" stroke-width="2"/>'
            f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="{BORDER}" stroke-width="2"/>')


def text(x, y, s, cls="sp-d", anchor="start", style="") -> str:
    st = f' style="{style}"' if style else ""
    return f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}"{st}>{esc(s)}</text>'


# ---------------------------------------------------------------- payoff
def payoff() -> str:
    """Long premium: the loss floor is flat, and it is the whole risk."""
    s = [head(label="Payoff of a long option: a flat maximum loss below the strike, "
                    "then profit rising without limit above it")]
    x0, x1, ymid, ylo = 70, 600, 170, 300
    s.append(f'<line x1="{x0}" y1="{ymid}" x2="{x1}" y2="{ymid}" stroke="{BORDER}" stroke-dasharray="5 5"/>')
    strike = 300
    # the floor, shaded
    s.append(f'<path d="M{x0},{ylo} L{strike},{ylo} L{strike},{ymid} L{x0},{ymid} Z" '
             f'fill="{STOP}" fill-opacity=".12"/>')
    s.append(f'<path d="M{x0},{ylo} L{strike},{ylo} L{x1},80" fill="none" stroke="{ACCENT}" '
             f'stroke-width="4.5" stroke-linejoin="round" stroke-linecap="round"/>')
    s.append(f'<line x1="{strike}" y1="60" x2="{strike}" y2="{ylo + 16}" stroke="{DIM}" '
             f'stroke-opacity=".5" stroke-dasharray="4 5"/>')
    s.append(text(strike + 10, 344, "strike", "sp-m"))
    s.append(text(x0 + 12, ylo - 18, "max loss = the premium paid", "sp-d", style=f"fill:{STOP}"))
    s.append(text(x1, 66, "upside", "sp-t", "end", style=f"fill:{ACCENT}"))
    s.append(text(x0, 44, "profit", "sp-m"))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- delta
def delta() -> str:
    """Where on the curve the tactics ask the model to sit."""
    s = [head(label="Delta across strikes: an S-curve, with the at-the-money point at 0.50 "
                    "and the chosen out-of-the-money point near 0.40")]
    x0, x1, y0, y1 = 70, 600, 60, 300
    s.append(axes(x0, y0, x1, y1))
    pts = []
    for i in range(61):
        t = i / 60
        d = 1 / (1 + pow(2.718281828, (t - 0.5) * 11))     # logistic, 1 -> 0
        pts.append((x0 + t * (x1 - x0), y1 - d * (y1 - y0)))
    s.append(f'<path d="M{" L".join(f"{x:.0f},{y:.0f}" for x, y in pts)}" fill="none" '
             f'stroke="{EXT}" stroke-width="4.5"/>')
    for frac, lab, colour in ((0.5, "0.50  at the money", ACCENT), (0.62, "0.40  the OTM pick", GATE)):
        px, py = pts[int(frac * 60)]
        s.append(f'<circle cx="{px:.0f}" cy="{py:.0f}" r="9" fill="{colour}" stroke="#0b1d33" stroke-width="3"/>')
        s.append(text(px + 16, py + 6, lab, "sp-d", style=f"fill:{colour}"))
    s.append(text(x0, 44, "|delta|", "sp-m"))
    s.append(text(x1, 336, "strike, further out of the money", "sp-m", "end"))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- grid
def grid() -> str:
    """The menu: what one page reached, against what the model now sees."""
    s = [head(label="A strike-by-expiry grid: one API page reached only the nearest expiries; "
                    "the menu now spans three expiries and both sides of the money")]
    cols, rows = 14, 6
    cw, ch, x0, y0 = 36, 34, 70, 70
    lit = {(r, c) for r in (0, 2, 4) for c in (5, 6, 8, 9)}
    page = {(r, c) for r in (0, 1) for c in range(cols)}
    for r in range(rows):
        for c in range(cols):
            x, y = x0 + c * (cw + 4), y0 + r * (ch + 4)
            if (r, c) in lit:
                fill, stroke, op = GATE, GATE, ".85"
            elif (r, c) in page:
                fill, stroke, op = EXT, EXT, ".34"
            else:
                fill, stroke, op = "none", BORDER, ".5"
            s.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" rx="6" fill="{fill}" '
                     f'fill-opacity="{op if fill != "none" else 0}" stroke="{stroke}" '
                     f'stroke-opacity="{op}"/>')
    s.append(text(x0, 48, "one API page", "sp-m", style=f"fill:{EXT}"))
    s.append(text(x0 + 250, 48, "the 12 the model sees", "sp-m", style=f"fill:{GATE}"))
    s.append(text(x0, y0 + rows * (ch + 4) + 30, "expiries → 45 days", "sp-m"))
    s.append(text(x0 + cols * (cw + 4) - 4, y0 + rows * (ch + 4) + 30, "strikes ±8%", "sp-m", "end"))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- prior
def prior() -> str:
    """Two crowds, and the one that had not traded enough to be worth hearing."""
    s = [head(label="Two implied distributions: a well-traded one that is shown to the model, "
                    "and a flat, barely-traded one that is withheld")]
    base = 250
    for ox, heights, colour, lab, sub in (
        (60, [8, 18, 38, 72, 108, 132, 104, 68, 34, 16], ACCENT, "shown", "volume 756"),
        (350, [46, 52, 48, 55, 50, 54, 49, 53, 47, 51], DIM, "withheld", "volume 45"),
    ):
        for i, h in enumerate(heights):
            x = ox + i * 24
            s.append(f'<rect x="{x}" y="{base - h}" width="17" height="{h}" rx="3" fill="{colour}" '
                     f'fill-opacity="{".85" if colour == ACCENT else ".28"}"/>')
        s.append(f'<line x1="{ox - 6}" y1="{base}" x2="{ox + 234}" y2="{base}" stroke="{BORDER}" stroke-width="2"/>')
        s.append(text(ox, base + 32, lab, "sp-t", style=f"fill:{colour if colour == ACCENT else STOP}"))
        s.append(text(ox, base + 58, sub, "sp-m"))
    s.append(text(60, 44, "a thin market still quotes every bucket", "sp-d"))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- brier
def brier() -> str:
    """The scale the priors are graded on, and where they landed."""
    s = [head(h=320, label="A Brier score scale from zero to a quarter: the day's priors sit "
                           "near zero, far from the coin-flip mark at 0.25")]
    x0, x1, y = 70, 590, 170
    s.append(f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0">'
             f'<stop offset="0" stop-color="{ACCENT}"/><stop offset="1" stop-color="{STOP}"/>'
             f'</linearGradient></defs>')
    s.append(f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="16" rx="8" fill="url(#g)" fill-opacity=".55"/>')
    def at(v):
        return x0 + (v / 0.25) * (x1 - x0)
    for v, lab, colour, up in ((0.004, "Kalshi 0.004", ACCENT, True), (0.008, "chain 0.008", EXT, False)):
        px = at(v)
        s.append(f'<line x1="{px:.0f}" y1="{y - 26 if up else y + 42}" x2="{px:.0f}" y2="{y + (0 if up else 16)}" '
                 f'stroke="{colour}" stroke-width="3"/>')
        s.append(f'<circle cx="{px:.0f}" cy="{y + 8}" r="9" fill="{colour}" stroke="#0b1d33" stroke-width="3"/>')
        s.append(text(px + 10, y - 32 if up else y + 62, lab, "sp-t", style=f"fill:{colour}"))
    s.append(f'<line x1="{x1}" y1="{y - 26}" x2="{x1}" y2="{y}" stroke="{STOP}" stroke-width="3"/>')
    s.append(text(x1, y - 34, "0.25  a coin flip", "sp-t", "end", style=f"fill:{STOP}"))
    s.append(text(x0 - 14, y + 16, "0", "sp-m", "end"))
    s.append(text(x0, 46, "Brier score - lower is better", "sp-d"))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- leash
def leash() -> str:
    """What ends a position, and who decides it."""
    s = [head(label="A position's price path between a take-profit band above and a stop-loss "
                    "band below, closed by code when it touches one")]
    x0, x1, mid = 70, 600, 190
    tp, sl = mid - 78, mid + 78
    s.append(f'<rect x="{x0}" y="{tp - 26}" width="{x1 - x0}" height="26" fill="{ACCENT}" fill-opacity=".14"/>')
    s.append(f'<rect x="{x0}" y="{sl}" width="{x1 - x0}" height="26" fill="{STOP}" fill-opacity=".14"/>')
    for yy, colour, lab in ((tp, ACCENT, "take-profit  +60%"), (sl + 26, STOP, "stop-loss  -40%")):
        s.append(f'<line x1="{x0}" y1="{yy}" x2="{x1}" y2="{yy}" stroke="{colour}" stroke-width="3" '
                 f'stroke-dasharray="10 7"/>')
        s.append(text(x0 + 6, yy - 10 if colour == ACCENT else yy + 26, lab, "sp-m", style=f"fill:{colour}"))
    s.append(f'<line x1="{x0}" y1="{mid}" x2="{x1}" y2="{mid}" stroke="{BORDER}" stroke-dasharray="4 6"/>')
    s.append(text(x1, mid - 10, "entry", "sp-m", "end"))
    path = [(x0 + 10, mid), (150, mid - 30), (230, mid + 10), (310, mid + 46), (390, mid + 30),
            (470, mid + 70), (520, sl + 26)]
    s.append(f'<path d="M{" L".join(f"{x},{y}" for x, y in path)}" fill="none" stroke="{MODEL}" '
             f'stroke-width="4" stroke-linejoin="round"/>')
    s.append(f'<circle cx="520" cy="{sl + 26}" r="11" fill="{STOP}" stroke="#0b1d33" stroke-width="3"/>')
    s.append(text(x1, sl + 62, "closed by code, not by the model", "sp-t", "end", style=f"fill:{STOP}"))
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- farm
def farm() -> str:
    """Three accounts, same market, one deliberate difference each."""
    s = [head(label="Three account equity lines leaving a common start and diverging over four "
                    "sessions")]
    x0, x1, y0, y1 = 70, 590, 60, 290
    mid = (y0 + y1) / 2
    s.append(f'<line x1="{x0}" y1="{mid}" x2="{x1}" y2="{mid}" stroke="{BORDER}" stroke-dasharray="5 6"/>')
    for pts, colour, lab in (
        ([0, 8, -6, 26, 40], ACCENT, "official"),
        ([0, -22, -48, -80, -104], MODEL, "test"),
        ([0, 14, 30, 58, 86], GATE, "mixed"),
    ):
        d = " L".join(f"{x0 + i * (x1 - x0) / 4:.0f},{mid - v:.0f}" for i, v in enumerate(pts))
        s.append(f'<path d="M{d}" fill="none" stroke="{colour}" stroke-width="4" stroke-linejoin="round"/>')
        s.append(f'<circle cx="{x1}" cy="{mid - pts[-1]}" r="7" fill="{colour}"/>')
        s.append(text(x1 - 8, mid - pts[-1] - 14, lab, "sp-m", "end", style=f"fill:{colour}"))
    s.append(text(x0, y1 + 34, "same market, same cadence, one difference each", "sp-d"))
    s.append("</svg>")
    return "".join(s)


SPOTS = {"payoff": payoff, "delta": delta, "grid": grid, "prior": prior,
         "brier": brier, "leash": leash, "farm": farm}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in SPOTS.items():
        path = OUT / f"spot-{name}.svg"
        path.write_text(fn())
        print(f"  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

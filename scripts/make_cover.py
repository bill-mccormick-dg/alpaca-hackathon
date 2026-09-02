#!/usr/bin/env python3
"""The submission cover image, drawn rather than screenshotted.

lablab's form wants a cover at 1280x720 or larger, and METADATA.md used to
plan for "a real status.py screenshot". A terminal at thumbnail size is an
unreadable grey rectangle, and a stock photo of a trading floor would be
somebody else's picture of somebody else's work.

So: the product's own idea, drawn. A price series runs up, rolls over and
falls - and stops dead on a line, held by a rope anchored above it. The
dashed ghost below the line is where it was going. That is the whole thesis
in one picture: the model may take the position, and the fall is caught by
code it does not control.

Same palette and type scale as the diagrams, so the cover, the deck and the
docs read as one set.

    python scripts/make_cover.py                 # -> submission/cover.svg
    python scripts/make_cover.py --png           # also rasterise via Chrome
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from render_diagrams import ACCENT, BORDER, DIM, EXT, FONT, GATE, MONO, STOP, esc

OUT = ROOT / "submission/cover.svg"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H = 1600, 900

# A deterministic series: a climb, a roll-over, and a drop that gets caught.
# Not real prices - an illustration, which is why it carries no axis, no ticker
# and no numbers anyone could mistake for a result.
#
# Every close sits ABOVE the catch line until the last one lands on it. That is
# the whole readability of the picture: a line the series has never touched,
# then a fall that stops on it. An earlier draft put the line mid-series and it
# just looked like a chart with a rule through it.
CLOSES = [118, 124, 121, 130, 136, 133, 142, 149,
          146, 155, 162, 159, 168, 152, 128, 112]
CATCH = 112                  # where the rope holds
GHOST = [96, 84, 70]         # where it was going


def scale(lo, hi, y0, y1):
    return lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)


def candles():
    """(x, open, close) per bar, with the chart geometry.

    The series stops well short of the right edge so the ghost fall has canvas
    to fall into - it ran off the frame when the bars used the full width."""
    x0, x1, y0, y1 = 110, 1250, 300, 700
    pitch = (x1 - x0) / len(CLOSES)
    width = pitch * 0.54
    lo, hi = min(GHOST) - 8, max(CLOSES) + 8
    y = scale(lo, hi, y0, y1)
    bars = []
    prev = CLOSES[0] - 5
    for i, close in enumerate(CLOSES):
        cx = x0 + pitch * (i + 0.5)
        bars.append((cx, prev, close))
        prev = close
    return bars, width, y


def cover() -> str:
    bars, cw, y = candles()
    s = [(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
          f'role="img" aria-label="A rising price series rolls over and falls, and is stopped on a '
          f'line held by a rope anchored above it; a dashed ghost shows where it was going">'),
         f'''<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#12345c"/><stop offset="55%" stop-color="#0b2340"/>
    <stop offset="100%" stop-color="#050f1c"/>
  </linearGradient>
  <radialGradient id="glow" cx="20%" cy="0%" r="85%">
    <stop offset="0" stop-color="#2a6cb0" stop-opacity=".45"/>
    <stop offset="1" stop-color="#2a6cb0" stop-opacity="0"/>
  </radialGradient>
</defs>
<style>
  .h {{ font-family: {FONT}; fill: #ffffff; font-weight: 800; }}
  .a {{ font-family: {FONT}; fill: {EXT}; font-weight: 700; }}
  .d {{ font-family: {FONT}; fill: {DIM}; }}
  .m {{ font-family: {MONO}; fill: {DIM}; }}
</style>
<rect width="{W}" height="{H}" fill="url(#bg)"/>
<rect width="{W}" height="{H}" fill="url(#glow)"/>''']

    # faint grid
    for i in range(6):
        gy = 320 + i * 78
        s.append(f'<line x1="80" y1="{gy}" x2="{W - 80}" y2="{gy}" stroke="{BORDER}" stroke-opacity=".26"/>')

    # the ghost fall - dashed, below the line, where it was going
    gx = bars[-1][0] + (bars[-1][0] - bars[-2][0])
    pitch = bars[1][0] - bars[0][0]
    trail = [(bars[-1][0], y(CATCH))]
    prev = CATCH
    for i, g in enumerate(GHOST):
        cx = gx + pitch * i
        trail.append((cx, y(g)))
        top, bot = y(max(prev, g)), y(min(prev, g))
        s.append(f'<rect x="{cx - cw/2:.0f}" y="{top:.0f}" width="{cw:.0f}" height="{max(bot - top, 3):.0f}" '
                 f'rx="3" fill="none" stroke="{STOP}" stroke-opacity=".5" stroke-dasharray="7 6" stroke-width="2.5"/>')
        prev = g
    # Right-aligned to the frame: left-aligned off the last ghost bar, this ran
    # off the canvas. A class's fill beats a presentation attribute, so the
    # colour has to be an inline style or it silently takes the class's.
    path = "M" + " L".join(f"{x:.0f},{yy:.0f}" for x, yy in trail)
    s.append(f'<path d="{path}" fill="none" stroke="{STOP}" stroke-opacity=".55" stroke-width="3" '
             f'stroke-dasharray="9 7" stroke-linejoin="round"/>')
    s.append(f'<text class="d" x="{W - 104}" y="{y(GHOST[-1]) + 52:.0f}" font-size="23" '
             f'text-anchor="end" style="fill:{STOP};fill-opacity:.9">without the rope</text>')

    # the candles
    for i, (cx, o, c) in enumerate(bars):
        up = c >= o
        colour = ACCENT if up else STOP
        top, bot = y(max(o, c)), y(min(o, c))
        wick_hi, wick_lo = top - 12 - (18 if not up else 8), bot + 10 + (16 if not up else 6)
        # No lower wick on the caught bar - a wick through the line it stopped
        # on argues the opposite of the picture.
        if i == len(bars) - 1:
            wick_lo = bot
        s.append(f'<line x1="{cx:.0f}" y1="{wick_hi:.0f}" x2="{cx:.0f}" y2="{wick_lo:.0f}" '
                 f'stroke="{colour}" stroke-width="2.5" stroke-opacity=".75"/>')
        s.append(f'<rect x="{cx - cw/2:.0f}" y="{top:.0f}" width="{cw:.0f}" height="{max(bot - top, 4):.0f}" '
                 f'rx="4" fill="{colour}" fill-opacity="{0.9 if up else 0.85}"/>')

    # the line it stops on, and the rope holding it. Three stacked strokes make
    # the glow - an SVG blur filter is heavier and rasterises unevenly.
    cy = y(CATCH)
    for width, opacity in ((16, .10), (8, .16)):
        s.append(f'<line x1="90" y1="{cy:.0f}" x2="{W - 90}" y2="{cy:.0f}" stroke="{GATE}" '
                 f'stroke-width="{width}" stroke-opacity="{opacity}" stroke-linecap="round"/>')
    s.append(f'<line x1="90" y1="{cy:.0f}" x2="{W - 90}" y2="{cy:.0f}" stroke="{GATE}" stroke-width="3.5" '
             f'stroke-dasharray="14 9"/>')
    anchor_x, anchor_y = 1418, 214
    clip_x = bars[-1][0]
    clip_top, clip_bot = cy - 84, cy - 24
    s.append(f'<path d="M{anchor_x},{anchor_y + 20} L{anchor_x - 52},{anchor_y + 128} '
             f'L{clip_x:.0f},{clip_top:.0f}" fill="none" stroke="{GATE}" stroke-width="6" '
             f'stroke-linejoin="round" stroke-linecap="round"/>')
    # the anchor it hangs from
    s.append(f'<line x1="{anchor_x - 54}" y1="{anchor_y}" x2="{anchor_x + 54}" y2="{anchor_y}" '
             f'stroke="{GATE}" stroke-width="7" stroke-linecap="round"/>')
    s.append(f'<circle cx="{anchor_x}" cy="{anchor_y + 22}" r="16" fill="none" stroke="{GATE}" stroke-width="6"/>')
    # the clip, biting the candle that was caught
    s.append(f'<rect x="{clip_x - 19:.0f}" y="{clip_top:.0f}" width="38" height="{clip_bot - clip_top:.0f}" '
             f'rx="19" fill="none" stroke="{GATE}" stroke-width="6"/>')
    # Below the line, not above it: above, it lands on top of the candles.
    s.append(f'<text class="a" x="112" y="{cy + 46:.0f}" font-size="26" style="fill:{GATE}">'
             f'check_order() - the fall stops here</text>')

    # wordmark
    s.append('<text class="h" x="110" y="188" font-size="112" letter-spacing="-3">Autobelay</text>')
    s.append('<text class="a" x="116" y="244" font-size="38">long premium, short leash</text>')
    s.append('<text class="d" x="116" y="296" font-size="25">'
             'An autonomous options agent on Alpaca\'s MCP server. An open model proposes; code disposes.</text>')

    # footer
    s.append(f'<line x1="110" y1="828" x2="114" y2="828" stroke="{EXT}" stroke-width="0"/>')
    s.append(f'<rect x="110" y="812" width="5" height="26" rx="2" fill="{EXT}"/>')
    s.append('<text class="d" x="130" y="833" font-size="24">Team RazorsEdge'
             ' &#183; Alpaca AI Trading Agents Hackathon 2026</text>')
    s.append(f'<text class="m" x="{W - 110}" y="833" font-size="22" text-anchor="end">'
             f'{esc("open-weight model + deterministic risk code")}</text>')
    s.append("</svg>")
    return "".join(s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--png", action="store_true", help="also rasterise to cover.png (needs Chrome)")
    args = ap.parse_args()

    OUT.write_text(cover())
    print(f"{OUT.relative_to(ROOT)}  {W}x{H}")
    if args.png:
        png = OUT.with_suffix(".png")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={W},{H}", f"--screenshot={png}", f"file://{OUT}"],
                       capture_output=True, check=False, timeout=120)
        print(f"{png.relative_to(ROOT)}  {'ok' if png.exists() else 'FAILED - is Chrome installed?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

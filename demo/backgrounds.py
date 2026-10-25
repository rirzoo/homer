"""Draw the background images in homer/tui/backgrounds/ as 1920x1080 SVG, faint enough for text to sit on:

    python3 demo/backgrounds.py /tmp/bg
    inkscape /tmp/bg/*.svg --export-type=png
    magick mogrify -path homer/tui/backgrounds -format webp -quality 85 /tmp/bg/*.png
"""
import random
import sys
from pathlib import Path

W, H = 1920, 1080
OUT = Path(sys.argv[1])


def svg(body, bg):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'<rect width="{W}" height="{H}" fill="{bg}"/>{body}</svg>')


def horizon_grid(top, color, op):
    """A floor grid running to a vanishing point at the horizon."""
    vx, s = W / 2, []
    for i in range(-24, 25):
        s.append(f'<line x1="{vx + i * 18:.0f}" y1="{top}" x2="{vx + i * 160:.0f}" y2="{H}"/>')
    y, gap = top + 4, 6
    while y < H:
        s.append(f'<line x1="0" y1="{y:.0f}" x2="{W}" y2="{y:.0f}"/>')
        gap *= 1.28
        y += gap
    return f'<g stroke="{color}" stroke-opacity="{op}" stroke-width="2">{"".join(s)}</g>'


def stars(n, seed, op):
    rnd = random.Random(seed)
    return "".join(f'<circle cx="{rnd.randrange(W)}" cy="{rnd.randrange(560)}" r="{rnd.choice((1, 1, 1.5, 2))}" '
                   f'fill="#f5e9ff" fill-opacity="{rnd.uniform(op / 3, op):.2f}"/>' for _ in range(n))


def synthwave_sunset():
    """Sky from violet to pink, a striped sun sinking behind mountains, a neon floor grid."""
    top = 640
    b = ('<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
         '<stop offset="0" stop-color="#0d0221"/><stop offset="0.55" stop-color="#2a0a4a"/>'
         '<stop offset="1" stop-color="#5c0a4e"/></linearGradient>'
         '<linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">'
         '<stop offset="0" stop-color="#f9c80e"/><stop offset="1" stop-color="#ff2a6d"/></linearGradient>'
         '<linearGradient id="floor" x1="0" y1="0" x2="0" y2="1">'
         '<stop offset="0" stop-color="#1b0b3a"/><stop offset="1" stop-color="#0d0221"/></linearGradient>'
         f'<clipPath id="sunclip"><rect x="0" y="0" width="{W}" height="{top}"/></clipPath></defs>')
    b += f'<rect width="{W}" height="{top}" fill="url(#sky)"/>' + stars(140, 3, 0.7)
    sun = f'<circle cx="{W / 2}" cy="{top - 40}" r="230" fill="url(#sun)" fill-opacity="0.75"/>'
    bands = "".join(f'<rect x="0" y="{top - 140 + i * 26 + i * i}" width="{W}" height="{4 + i * 2}" fill="#2a0a4a"/>'
                    for i in range(6))
    b += f'<g clip-path="url(#sunclip)">{sun}{bands}</g>'
    b += (f'<polygon points="0,{top} 220,{top - 120} 360,{top - 40} 520,{top - 170} 700,{top} '
          f'1200,{top} 1380,{top - 150} 1520,{top - 60} 1690,{top - 190} 1920,{top - 30} 1920,{top}" '
          f'fill="#12052a" stroke="#05d9e8" stroke-opacity="0.45" stroke-width="2"/>')
    b += f'<rect y="{top}" width="{W}" height="{H - top}" fill="url(#floor)"/>' + horizon_grid(top, "#ff2a6d", 0.5)
    b += f'<line x1="0" y1="{top}" x2="{W}" y2="{top}" stroke="#ff2a6d" stroke-opacity="0.8" stroke-width="3"/>'
    return svg(b, "#0d0221")


def synthwave_night():
    """Just the neon floor under a starry sky, darker, for busy mailboxes."""
    top = 560
    b = ('<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
         '<stop offset="0" stop-color="#05010f"/><stop offset="1" stop-color="#1b0b3a"/></linearGradient></defs>')
    b += f'<rect width="{W}" height="{top}" fill="url(#sky)"/>' + stars(260, 11, 0.8)
    b += f'<rect y="{top}" width="{W}" height="{H - top}" fill="#0d0221"/>' + horizon_grid(top, "#7a04eb", 0.55)
    b += f'<line x1="0" y1="{top}" x2="{W}" y2="{top}" stroke="#05d9e8" stroke-opacity="0.7" stroke-width="2"/>'
    return svg(b, "#05010f")


for name, fn in (("synthwave-sunset", synthwave_sunset), ("synthwave-night", synthwave_night)):
    (OUT / f"{name}.svg").write_text(fn())

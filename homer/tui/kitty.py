"""The kitty window ([tui] window = "kitty"). A terminal draws every character, so a theme alone can't change the
font or put a picture behind the text. kitty can, per window and while it runs, so homer opens itself in a kitty
window of its own and dresses it for the theme: the theme's font, a background image, and the theme's big areas
(the page, panels, mail cards) see-through so the image shows behind them."""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from textual.color import Color

HERE = Path(__file__).parent
FONTS = Path("~/.local/share/fonts/homer").expanduser()

# Per theme: a font homer ships (fonts/), its size in points, the backgrounds to pick from (backgrounds/<theme>-<name>)
# and the veil, how much of the page colour lies over the image
LOOK = {
    "phosphor": {"font": "VT323", "size": 15},
    "blueprint": {"font": "Share Tech Mono", "size": 12},
    "synthwave": {"font": "Space Mono", "size": 11, "images": ("sunset", "night"), "veil": 0.5},
    "paper": {"font": "Monaspace Radon", "size": 11},
}


def wanted(cfg):
    """Asked for in the config, and this isn't already the window homer opened."""
    return cfg.get("tui", {}).get("window") == "kitty" and "HOMER_KITTY" not in os.environ


def inside():
    return "HOMER_KITTY" in os.environ


def images(theme):
    return LOOK.get(theme, {}).get("images", ())


def install_fonts():
    """kitty finds fonts through fontconfig, so the ones homer ships go into ~/.local/share/fonts/homer."""
    new = [f for f in (HERE / "fonts").iterdir() if f.suffix in (".ttf", ".otf") and not (FONTS / f.name).exists()]
    if new:
        FONTS.mkdir(parents=True, exist_ok=True)
        for f in new:
            shutil.copy2(f, FONTS)
        subprocess.run(["fc-cache", str(FONTS)], capture_output=True)


def open_window():
    """Start homer again, with the same arguments, in a kitty window of its own. False when kitty isn't installed.
    The window listens on a socket of its own, so dressing it never writes to the terminal Textual is reading."""
    if not shutil.which("kitty"):
        return False
    install_fonts()
    env = {**os.environ, "HOMER_KITTY": "1", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    subprocess.Popen(["kitty", "--detach", "--title", "homer", "-o", "allow_remote_control=socket-only",
                      "--listen-on", f"unix:@homer-{uuid.uuid4().hex}", sys.executable, "-m", "homer", *sys.argv[1:]],
                     env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def see_through(v, veil):
    """kitty's transparent_background_colors: the colours homer fills big areas with, as Textual paints them, each
    with how much of it covers the image. The page is mostly image; cards are glass; header and panels stay solid
    enough to read on."""
    page = Color.parse(v["background"])

    def painted(spec):  # "#4fc3f7 7%" is blended over the page the way Textual does it
        colour, _, alpha = spec.partition(" ")
        return page + Color.parse(colour).with_alpha(float(alpha.rstrip("%")) / 100) if alpha else Color.parse(colour)

    focused = page.tint(Color.parse(v["foreground"]).with_alpha(0.05))  # Textual tints a focused list this much
    pairs = {page.hex: veil, focused.hex: veil, Color.parse(v["surface"]).hex: 0.85, Color.parse(v["panel"]).hex: 0.8}
    if v["homer-card-bg"] != "transparent":
        pairs.setdefault(painted(v["homer-card-bg"]).hex, min(1, veil + 0.25))
    pairs.setdefault(painted(v["homer-bar"]).hex, 0.75)
    return " ".join(f"{c}@{o:g}" for c, o in pairs.items())  # kitty takes at most 7


def look(theme, pick, v):
    """The kitty options for a theme and background pick, given the theme's CSS variables. Every option is always
    set, so switching back to a plain theme undoes the last one."""
    k = LOOK.get(theme, {})
    image = HERE / "backgrounds" / f"{theme}-{pick}.webp" if pick in k.get("images", ()) else None
    return [f"font_family={k.get('font', 'monospace')}", f"font_size={k.get('size', 11)}",
            f"background_image={image or 'none'}", "background_image_layout=cscaled",
            f"transparent_background_colors={see_through(v, k.get('veil', 0.4)) if image else ''}"]


def dress(options):
    subprocess.Popen(["kitten", "@", "load-config", *(x for o in options for x in ("-o", o))],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

"""Draw the pointer tool's icon: a laser pointer with a red beam and dot.

Drawn at 512 px and downsampled, so edges are smooth. Run:
  uv run --no-project --with pillow==11.3.0 python tools/draw_pointer.py
Writes mod/kc-companion/graphics/pointer.png (64 px: item, cursor, shortcut, Point button).
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 512
OUT = Path(__file__).resolve().parents[1] / "mod" / "kc-companion" / "graphics"

LINE = (30, 32, 38)
METAL = (120, 128, 140)
METAL_LIGHT = (200, 208, 218)
METAL_DARK = (70, 76, 86)
RED = (255, 40, 40)
RED_GLOW = (255, 60, 50)

# the body runs from the back end (bottom-left) to the tip; the beam goes on to the dot (top-right)
BACK, TIP, DOT = (64, 448), (300, 212), (446, 66)
U = (1 / math.sqrt(2), -1 / math.sqrt(2))   # along the body
N = (1 / math.sqrt(2), 1 / math.sqrt(2))    # across it


def at(p, a=0.0, b=0.0):
    """Point p moved a along the body and b across it."""
    return (p[0] + U[0] * a + N[0] * b, p[1] + U[1] * a + N[1] * b)


def band(d, p0, p1, w, **kw):
    d.polygon([at(p0, 0, w), at(p1, 0, w), at(p1, 0, -w), at(p0, 0, -w)], **kw)


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # beam and dot glow, blurred under the sharp parts
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.line([TIP, DOT], fill=RED_GLOW + (200,), width=34)
    g.ellipse((DOT[0] - 70, DOT[1] - 70, DOT[0] + 70, DOT[1] + 70), fill=RED_GLOW + (220,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))

    d = ImageDraw.Draw(img)
    d.line([TIP, DOT], fill=RED + (255,), width=14)
    d.line([TIP, DOT], fill=(255, 200, 200, 255), width=5)
    d.ellipse((DOT[0] - 40, DOT[1] - 40, DOT[0] + 40, DOT[1] + 40), fill=RED + (255,))
    d.ellipse((DOT[0] - 18, DOT[1] - 18, DOT[0] + 18, DOT[1] + 18), fill=(255, 235, 235, 255))

    # body: outline, metal, highlight stripe, shadow stripe
    tail = at(BACK, -6)
    band(d, tail, at(TIP, 6), 52, fill=LINE)
    band(d, BACK, TIP, 42, fill=METAL)
    band(d, at(BACK, 6), at(TIP, -6), 14, fill=METAL_LIGHT)
    band(d, at(BACK, 6, 0), at(TIP, -6, 0), 8, fill=METAL_LIGHT)
    d.polygon([at(BACK, 0, -42), at(TIP, 0, -42), at(TIP, 0, -22), at(BACK, 0, -22)], fill=METAL_DARK)
    # head: a darker ring before the tip, the lens at the tip
    band(d, at(TIP, -58), at(TIP, -8), 46, fill=METAL_DARK)
    band(d, at(TIP, -50), at(TIP, -40), 46, fill=LINE)
    lens = at(TIP, 2)
    d.ellipse((lens[0] - 24, lens[1] - 24, lens[0] + 24, lens[1] + 24), fill=RED + (255,), outline=LINE, width=6)
    # the button
    btn = at(BACK, 150, -30)
    d.ellipse((btn[0] - 20, btn[1] - 20, btn[0] + 20, btn[1] + 20), fill=(220, 30, 30, 255), outline=LINE, width=6)
    # back cap
    band(d, at(BACK, -6), at(BACK, 22), 48, fill=METAL_DARK)

    OUT.mkdir(parents=True, exist_ok=True)
    img.resize((64, 64), Image.LANCZOS).save(OUT / "pointer.png")
    img.resize((256, 256), Image.LANCZOS).save(Path(__file__).with_name("pointer-256.png"))
    print(OUT / "pointer.png")


if __name__ == "__main__":
    main()

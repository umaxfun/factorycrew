"""Draw Kira's icon: a chibi face with ginger hair and a cog hair clip.

Drawn at 512 px and downsampled, so edges are smooth. Run:
  uv run --no-project --with pillow==11.3.0 python tools/draw_kira.py
Writes mod/kc-companion/graphics/kira.png (64 px, the GUI sprite) and kira-256.png (preview).
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 512
OUT = Path(__file__).resolve().parents[1] / "mod" / "kc-companion" / "graphics"

HAIR = (232, 124, 52)
HAIR_DARK = (150, 62, 24)
HAIR_LIGHT = (255, 170, 95)
SKIN = (255, 226, 204)
SKIN_SHADE = (246, 196, 170)
LINE = (92, 44, 30)
IRIS = (38, 150, 160)
IRIS_DARK = (18, 80, 92)
BLUSH = (255, 128, 150, 120)
COG = (255, 200, 60)
COG_DARK = (170, 110, 20)


def ell(d, cx, cy, rx, ry, **kw):
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), **kw)


def cog(d, cx, cy, r, teeth=8):
    pts = []
    for i in range(teeth * 2):
        a = math.pi * i / teeth
        rr = r if i % 2 == 0 else r * 0.74
        for da in (-0.17, 0.17):
            pts.append((cx + rr * math.cos(a + da), cy + rr * math.sin(a + da)))
    d.polygon(pts, fill=COG, outline=COG_DARK, width=6)
    ell(d, cx, cy, r * 0.32, r * 0.32, fill=HAIR, outline=COG_DARK, width=6)


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # back hair: a big soft bob behind the head, longer at the sides
    d.rounded_rectangle((70, 90, 442, 470), radius=170, fill=HAIR, outline=HAIR_DARK, width=10)

    # face
    ell(d, 256, 292, 150, 142, fill=SKIN, outline=LINE, width=8)
    # soft shadow under the bangs
    shade = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shade).ellipse((130, 170, 382, 250), fill=SKIN_SHADE + (255,))
    shade = shade.filter(ImageFilter.GaussianBlur(10))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((110, 154, 402, 430), fill=255)
    img.paste(shade, (0, 0), Image.composite(shade, Image.new("RGBA", (S, S)), mask))
    d = ImageDraw.Draw(img)

    # bangs: one shape with a scalloped lower edge, so no strand lines cross the face
    top = [(256 + 168 * math.cos(a), 250 - 175 * math.sin(a))
           for a in [math.pi * i / 24 for i in range(0, 25)]]  # right -> left over the top
    tips = [(96, 262), (128, 214), (160, 250), (196, 206), (232, 252), (268, 204),
            (306, 248), (342, 208), (376, 246), (404, 214), (418, 258)]
    d.polygon(top[::-1] + tips[::-1], fill=HAIR, outline=HAIR_DARK, width=9)
    # side locks framing the cheeks, tapering to a point
    for sx in (-1, 1):
        x0 = 256 + sx * 152
        d.polygon([(x0 + sx * 8, 200), (x0 + sx * 16, 300), (x0 + sx * 2, 420),
                   (x0 - sx * 22, 340), (x0 - sx * 30, 230)], fill=HAIR, outline=HAIR_DARK, width=8)
    # shine on the hair
    d.arc((150, 96, 330, 210), 200, 260, fill=HAIR_LIGHT, width=14)

    # eyes
    for ex in (192, 320):
        ell(d, ex, 300, 40, 50, fill=(255, 255, 255), outline=LINE, width=6)
        ell(d, ex, 308, 32, 42, fill=IRIS)
        ell(d, ex, 320, 18, 26, fill=IRIS_DARK)
        ell(d, ex - 11, 288, 12, 14, fill=(255, 255, 255))
        ell(d, ex + 12, 330, 6, 6, fill=(255, 255, 255))
        # upper lash line, thick and curved
        d.arc((ex - 46, 246, ex + 46, 330), 200, 340, fill=LINE, width=13)
        lash = 1 if ex > 256 else -1
        d.line([(ex + lash * 44, 270), (ex + lash * 58, 258)], fill=LINE, width=9)

    # blush
    blush = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    for bx in (160, 352):
        ell(bd, bx, 362, 30, 16, fill=BLUSH)
    img.alpha_composite(blush.filter(ImageFilter.GaussianBlur(4)))
    d = ImageDraw.Draw(img)

    # small open smile
    d.chord((232, 360, 280, 400), 0, 180, fill=(220, 90, 100), outline=LINE, width=6)
    d.line([(234, 380), (278, 380)], fill=LINE, width=6)

    # cog hair clip (it is Factorio after all)
    cog(d, 382, 150, 44)
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.resize((64, 64), Image.LANCZOS).save(OUT / "kira.png")
    big.resize((256, 256), Image.LANCZOS).save(OUT.parent.parent.parent / "tools" / "kira-256.png")
    print("wrote", OUT / "kira.png")


if __name__ == "__main__":
    main()

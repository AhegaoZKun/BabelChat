"""Generate the in-game addon icon (minimap button) from the app brand icon.

WoW cannot read PNG — textures must be BLP or uncompressed 32-bit TGA, and the
dimensions must be powers of two. The legacy `img/logo_wt.tga` was 500x500 and
carried Pirson's "WoW Translator" wordmark, which is both off-brand and
illegible at the ~20px the minimap button actually renders at.

This script derives a round minimap icon from `assets/icon.png` (the Tower of
Babel brand art): crop the art out of its rounded-square frame, mask it to a
circle, ring it in the brand gold, and lift contrast so the silhouette still
reads when it is 20 pixels wide.

Usage:  python tools/make_addon_icon.py [--preview]
Output: addon/BabelChat/img/icon.tga  (128x128, 32-bit uncompressed, top-left)
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "icon.png"
TARGET = ROOT / "addon" / "BabelChat" / "img" / "icon.tga"
PREVIEW_DIR = ROOT / "tools"

# Crop straight onto the tower rather than the whole framed plate: at the ~20px
# a minimap button actually renders, empty sky is what kills legibility, and the
# tapered stack is the only shape that survives the downscale.
CROP = (180, 150, 620, 590)

SUPERSAMPLE = 512
FINAL_SIZE = 128

GOLD = (201, 168, 106, 255)  # brand frame gold
GOLD_DARK = (120, 96, 52, 255)  # inner shade so the ring reads as metal
OUTLINE = (10, 9, 16, 255)  # dark edge — keeps the icon legible on snow/sand


def build() -> Image.Image:
    art = Image.open(SOURCE).convert("RGBA").crop(CROP)
    art = art.resize((SUPERSAMPLE, SUPERSAMPLE), Image.LANCZOS)

    # The tower is dark-on-dark at small sizes; lift contrast and saturation so
    # the teal glow carries the silhouette.
    art = ImageEnhance.Contrast(art).enhance(1.35)
    art = ImageEnhance.Color(art).enhance(1.40)
    art = ImageEnhance.Brightness(art).enhance(1.06)

    canvas = Image.new("RGBA", (SUPERSAMPLE, SUPERSAMPLE), (0, 0, 0, 0))

    # Circular mask, inset so the ring has room to sit inside the texture.
    mask = Image.new("L", (SUPERSAMPLE, SUPERSAMPLE), 0)
    ImageDraw.Draw(mask).ellipse((10, 10, SUPERSAMPLE - 10, SUPERSAMPLE - 10), fill=255)
    canvas.paste(art, (0, 0), mask)

    draw = ImageDraw.Draw(canvas)
    # Thin rim only — a fat frame eats the art at minimap size. Dark edge first
    # so the icon still separates from snow, sand and daylight water.
    draw.ellipse((4, 4, SUPERSAMPLE - 4, SUPERSAMPLE - 4), outline=OUTLINE, width=10)
    draw.ellipse((12, 12, SUPERSAMPLE - 12, SUPERSAMPLE - 12), outline=GOLD, width=14)
    draw.ellipse((24, 24, SUPERSAMPLE - 24, SUPERSAMPLE - 24), outline=GOLD_DARK, width=4)

    return canvas.resize((FINAL_SIZE, FINAL_SIZE), Image.LANCZOS)


def write_tga(img: Image.Image, path: Path) -> None:
    """Write a 32-bit uncompressed TGA with top-left origin.

    Pillow's own TGA writer defaults to bottom-left origin; WoW is happier with
    the same top-left layout the previous asset used (descriptor 0x28).
    """
    w, h = img.size
    header = struct.pack(
        "<BBBHHBHHHHBB",
        0,  # id length
        0,  # no colour map
        2,  # uncompressed true-colour
        0,
        0,
        0,  # colour map spec
        0,
        0,  # x/y origin
        w,
        h,
        32,  # bits per pixel
        0x28,  # top-left origin, 8 alpha bits
    )
    pixels = img.convert("RGBA").tobytes()
    bgra = bytearray(len(pixels))
    bgra[0::4] = pixels[2::4]
    bgra[1::4] = pixels[1::4]
    bgra[2::4] = pixels[0::4]
    bgra[3::4] = pixels[3::4]
    path.write_bytes(header + bytes(bgra))


def main() -> None:
    icon = build()
    write_tga(icon, TARGET)
    print(f"wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size:,} bytes)")

    # Legibility check: the minimap button renders at roughly 20px. Off by
    # default so a plain run leaves no untracked PNGs behind.
    if "--preview" in sys.argv:
        for size in (64, 32, 20):
            preview = icon.resize((size, size), Image.LANCZOS)
            out = PREVIEW_DIR / f"_icon_preview_{size}.png"
            preview.resize((size * 4, size * 4), Image.NEAREST).save(out)
            print(f"preview {size}px -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

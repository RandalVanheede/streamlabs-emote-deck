"""Draws the app icon: assets/icon.png (1024px), icon.icns (macOS), icon.ico (Windows). Run: python scripts/make_icon.py"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 2048  # drawn at 2x, downsampled for smooth edges
OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def vgradient(size, top, bottom):
    w, h = size
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        col.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return col.resize((w, h))


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Body: macOS icon grid, 824/1024 of the canvas, with a soft drop shadow
    inset, body = S * 100 // 1024, S * 824 // 1024
    radius = body * 185 // 824
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((inset, inset + 24, inset + body, inset + body + 24), radius, fill=(0, 0, 0, 140))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
    img.paste(vgradient((body, body), (0x2E, 0x29, 0x47), (0x18, 0x16, 0x25)), (inset, inset), rounded_mask((body, body), radius))

    # 2x2 pads; the top-left one is lit amber and carries an emote face
    gap, margin = body * 52 // 824, body * 110 // 824
    pad = (body - 2 * margin - gap) // 2
    pr = pad * 22 // 100
    for row in range(2):
        for col in range(2):
            x = inset + margin + col * (pad + gap)
            y = inset + margin + row * (pad + gap)
            lit = row == 0 and col == 0
            under = Image.new("RGBA", (S, S), (0, 0, 0, 0))  # keycap depth
            ImageDraw.Draw(under).rounded_rectangle((x, y + pad // 14, x + pad, y + pad + pad // 14), pr, fill=(0x12, 0x10, 0x1C, 255))
            img.alpha_composite(under)
            if lit:
                glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
                ImageDraw.Draw(glow).rounded_rectangle((x - 30, y - 30, x + pad + 30, y + pad + 30), pr + 30, fill=(0xFF, 0xB2, 0x3E, 170))
                img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(60)))
                face = vgradient((pad, pad), (0xFF, 0xCE, 0x75), (0xFF, 0xA3, 0x1A))
            else:
                face = vgradient((pad, pad), (0x47, 0x40, 0x69), (0x30, 0x2B, 0x49))
            img.paste(face, (x, y), rounded_mask((pad, pad), pr))
            if lit:
                d, ink, w = ImageDraw.Draw(img), (0x2A, 0x1A, 0x00, 255), pad // 13
                cx, cy, e = x + pad // 2, y + pad // 2, pad // 16
                for ex in (cx - pad // 6, cx + pad // 6):
                    d.ellipse((ex - e, cy - pad // 9 - e, ex + e, cy - pad // 9 + e), fill=ink)
                r = pad // 4
                ay = cy + pad // 20
                d.arc((cx - r, ay - r, cx + r, ay + r), 25, 155, fill=ink, width=w)
                for a in (25, 155):  # round caps on the smile
                    px = cx + (r - w / 2) * math.cos(math.radians(a))
                    py = ay + (r - w / 2) * math.sin(math.radians(a))
                    d.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=ink)

    OUT.parent.mkdir(exist_ok=True)
    icon = img.resize((1024, 1024), Image.LANCZOS)
    icon.save(OUT)
    # Native formats so PyInstaller uses them as-is (no conversion step that depends on its environment)
    icon.save(OUT.with_suffix(".icns"))
    icon.save(OUT.with_suffix(".ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT} (+ .icns, .ico)")


if __name__ == "__main__":
    main()

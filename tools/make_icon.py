"""Generuje ikonę programu (assets/laserburn.ico + .png). Uruchom: python tools/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
S = 1024  # rysujemy w dużej rozdzielczości i zmniejszamy


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # tło: ciemna płytka ze sklejki
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=190, fill=(34, 30, 28, 255))
    d.rounded_rectangle((90, 560, S - 90, S - 120), radius=60, fill=(196, 150, 98, 255))
    for y in range(600, S - 140, 46):  # słoje
        d.line((130, y, S - 130, y + 18), fill=(176, 130, 80, 255), width=10)
    # wypalona ścieżka (serce) na płytce
    d.line((230, 780, 420, 690, 600, 820, 790, 700), fill=(52, 30, 18, 255), width=34, joint="curve")
    # głowica lasera
    d.rounded_rectangle((392, 110, 632, 300), radius=36, fill=(70, 74, 84, 255))
    d.polygon(((432, 300), (592, 300), (548, 380), (476, 380)), fill=(98, 104, 116, 255))
    # wiązka z poświatą
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.line((512, 380, 600, 820), fill=(255, 40, 30, 200), width=70)
    g.ellipse((540, 760, 660, 880), fill=(255, 120, 40, 230))
    glow = glow.filter(ImageFilter.GaussianBlur(28))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    d.line((512, 380, 600, 820), fill=(255, 70, 50, 255), width=18)
    d.ellipse((574, 794, 626, 846), fill=(255, 245, 200, 255))
    return img


def main():
    big = draw()
    out_png = ROOT / "assets" / "laserburn.png"
    out_ico = ROOT / "assets" / "laserburn.ico"
    out_png.parent.mkdir(exist_ok=True)
    big.resize((256, 256), Image.LANCZOS).save(out_png)
    big.resize((256, 256), Image.LANCZOS).save(
        out_ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("zapisano", out_png, out_ico)


if __name__ == "__main__":
    main()

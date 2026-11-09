"""Draw the Matchroom Next icon: a graphite tile with two fort towers forming an
"M" and the app's amber accent. Original artwork, not the game's emblem.

Writes assets/matchroom-next.ico (16-256 px), assets/matchroom-next-512.png and
forts_icon.png (64 px, used inside the app and for Windows notifications).
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024
BG = (22, 26, 31, 255)        # --bg-2
RIM = (46, 54, 64, 255)       # --bg-5
TOWER = (232, 236, 239, 255)  # --fg-1
ACCENT = (242, 169, 59, 255)  # --accent


def draw(size=S):
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    k = size / S
    box = lambda *v: [round(x * k) for x in v]
    d.rounded_rectangle(box(40, 40, 984, 984), radius=round(210 * k), fill=BG, outline=RIM, width=max(1, round(18 * k)))
    # Two towers with battlements.
    for left in (238, 626):
        right = left + 160
        d.rectangle(box(left, 330, right, 790), fill=TOWER)
        for i in range(3):
            x = left + i * 60
            d.rectangle(box(x, 262, x + 40, 340), fill=TOWER)
    # Amber chevron between the towers completes the "M".
    d.polygon(box(398, 352, 512, 520, 626, 352, 626, 492, 512, 660, 398, 492), fill=ACCENT)
    # Ground line in amber.
    d.rounded_rectangle(box(200, 812, 824, 860), radius=round(24 * k), fill=ACCENT)
    return image


def main():
    big = draw(S)
    big.resize((512, 512), Image.LANCZOS).save(ROOT / 'assets' / 'matchroom-next-512.png')
    big.resize((64, 64), Image.LANCZOS).save(ROOT / 'forts_icon.png')
    sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]
    big.resize((256, 256), Image.LANCZOS).save(ROOT / 'assets' / 'matchroom-next.ico', sizes=sizes)
    print('icon written')


if __name__ == '__main__':
    main()

"""Draw scripts/console-assets/rebuild-console.ico, the Rebuild Console's icon.

A Meridian-blue tile with a white M over a red dimension line: the blue and the
dimension red of the Meridian sheets. The letter is drawn as polygons rather
than set in a font, so it renders the same on every machine and holds up at
16 px. Run from the repository root with the project's virtualenv (Pillow is in
requirements.txt):

    python scripts/console-assets/make-icon.py
"""
import sys
from PIL import Image, ImageDraw

out = sys.argv[1] if len(sys.argv) > 1 else "scripts/console-assets/rebuild-console.ico"
S = 1024  # drawn large, scaled down per size
BLUE, WHITE, RED = (31, 78, 156, 255), (255, 255, 255, 255), (229, 92, 76, 255)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=150, fill=BLUE)
# The M: two posts and a V, as polygons so it survives 16 px.
L, R, T, B, W = 230, S - 230, 250, 690, 120
d.polygon([(L, B), (L, T), (L + W, T), (S / 2, 560), (R - W, T), (R, T), (R, B),
           (R - W, B), (R - W, 470), (S / 2, 700), (L + W, 470), (L + W, B)], fill=WHITE)
# A drafting dimension line under it: the red that marks dimensions on every Meridian sheet.
y, h = 800, 44
d.rectangle([L, y, R, y + h], fill=RED)
d.rectangle([L, y - 50, L + 36, y + h + 50], fill=RED)
d.rectangle([R - 36, y - 50, R, y + h + 50], fill=RED)
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save(out, format="ICO", sizes=sizes)

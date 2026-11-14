"""Dev: UEFN designer capture (cropped to the 1280x720 screen frame) above my render."""
import sys
from PIL import Image

uefn, mine, out = sys.argv[1:4]
box = tuple(int(v) for v in sys.argv[4].split(",")) if len(sys.argv) > 4 else (432, 413, 1071, 772)
u = Image.open(uefn).convert("RGB").crop(box)
m = Image.open(mine).convert("RGB")
W = 1100
u2 = u.resize((W, round(W * u.height / u.width)), Image.LANCZOS)
m2 = m.resize((W, round(W * m.height / m.width)), Image.LANCZOS)
canvas = Image.new("RGB", (W, u2.height + m2.height + 6), (255, 0, 255))
canvas.paste(u2, (0, 0))
canvas.paste(m2, (0, u2.height + 6))
canvas.save(out)
print(u.size, m.size)

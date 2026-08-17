import sys
import numpy as np
from PIL import Image, ImageDraw
import omr, heads, paths

if len(sys.argv) < 3:
    raise SystemExit("usage: python3 inspect_system.py <p1|p2> <system-index>")
paths.ensure_dirs()
path, si = str(paths.page(sys.argv[1])), int(sys.argv[2])
ink, gray = omr.load_binary(path)
systems = omr.build_systems(ink)
s = systems[si]
space = float(np.mean([np.mean(np.diff(l)) for l in s["lines"]]))
top = min(l[0] for l in s["lines"])
bot = max(l[4] for l in s["lines"])
pad = int(space * 6)
y0b, y1b = max(0, top - pad), min(ink.shape[0], bot + pad)
H, hn = heads.find_heads(ink, space, y0b, y1b)
x0, x1 = min(s["xs"]), max(s["xs"])
H = [h for h in H if 150 <= h["x"] <= x1 + space * 6]
bars = heads.find_barlines(hn, s, space, [h["x"] for h in H])
bars = [b for b in bars if x0 - space * 3 <= b <= x1 + space * 6]

print(f"space={space:.1f} heads={len(H)} barlines={len(bars)} -> {[int(b) for b in bars]}")

im = Image.open(path).convert("RGB").crop((int(x0 - space * 4), y0b, ink.shape[1], y1b))
d = ImageDraw.Draw(im)
ox, oy = int(x0 - space * 4), y0b
for b in bars:
    d.line([(b - ox, 0), (b - ox, im.size[1])], fill=(0, 150, 255), width=4)
seq = []
for h in H:
    st = omr.step_at(s, h["x"], h["y"])
    l, o = heads.step_to_note(st)
    seq.append(f"{l}{o}")
    x, y = h["x"] - ox, h["y"] - oy
    d.ellipse([x - 15, y - 13, x + 15, y + 13], outline=(230, 20, 20), width=3)
    d.text((x - 12, y - 40), f"{l}{o}", fill=(190, 0, 0))
im.save(paths.OVERLAYS / f"dbg_{si}.png")
print(" ".join(seq))

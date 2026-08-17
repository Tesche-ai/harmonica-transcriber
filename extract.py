"""Per-system extraction: noteheads, barlines, accidental glyphs -> overlay + text.

Pitch letters come from the geometry (reliable). Accidentals are only *located*
here, not classified -- sharp/natural/flat are read off the overlay by eye,
which is quick because each one is boxed and numbered.
"""
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
import omr, heads, paths


def system_space(s):
    return float(np.mean([np.mean(np.diff(l)) for l in s["lines"]]))


def merge_split_systems(systems, tol=90):
    """The page curl can split one staff into two half-width detections."""
    out = []
    for s in systems:
        if out and abs(out[-1]["c"] - s["c"]) < tol:
            prev = out[-1]
            order = np.argsort(prev["xs"] + s["xs"])
            xs = np.array(prev["xs"] + s["xs"])[order].tolist()
            ls = [(prev["lines"] + s["lines"])[i] for i in order]
            prev["xs"], prev["lines"] = xs, ls
            prev["c"] = float(np.mean([np.mean(l) for l in ls]))
            omr.fit_system(prev)
        else:
            out.append(s)
    return out


def find_accidentals(noline, space, ymin, ymax):
    """Tall, narrow glyphs: sharps, naturals, flats (and key-signature sharps)."""
    band = np.zeros_like(noline)
    band[ymin:ymax] = noline[ymin:ymax]
    lab, n = ndi.label(band)
    out = []
    for sl, i in zip(ndi.find_objects(lab), range(1, n + 1)):
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        if not (space * 1.35 <= h <= space * 3.1):
            continue
        if not (space * 0.30 <= w <= space * 1.05):
            continue
        if w > h * 0.75:
            continue
        area = (lab[sl] == i).sum()
        if area < space * space * 0.25:
            continue
        mask = (lab[sl] == i)
        out.append({"x": (xs.start + xs.stop) / 2.0,
                    "y": (ys.start + ys.stop) / 2.0,
                    "x0": xs.start, "x1": xs.stop,
                    "y0": ys.start, "y1": ys.stop,
                    "kind": classify_accidental(mask)})
    out.sort(key=lambda d: d["x"])
    return out


def classify_accidental(mask):
    """sharp / natural / flat from the vertical extent of the left and right strokes.

    A sharp's two verticals both run the full height; a natural's left stroke
    stops short of the bottom and its right stroke starts below the top; a flat
    has a full-height left stroke and ink only in the lower half on the right.
    """
    h, w = mask.shape
    if h < 6 or w < 3:
        return "?"

    def extent(sub):
        rows = np.flatnonzero(sub.any(axis=1))
        if len(rows) == 0:
            return None
        return rows[0] / (h - 1), rows[-1] / (h - 1)

    lo = extent(mask[:, :max(1, int(w * 0.38))])
    hi = extent(mask[:, w - max(1, int(w * 0.38)):])
    if lo is None or hi is None:
        return "?"
    l_top, l_bot = lo
    r_top, r_bot = hi
    if l_bot > 0.88 and r_bot > 0.88 and r_top < 0.22:
        return "sharp"
    if l_bot < 0.86 and r_top > 0.14:
        return "natural"
    if l_bot > 0.85 and r_top > 0.30:
        return "flat"
    return "sharp" if r_top < 0.25 else "natural"


def run(path, tag):
    ink, gray = omr.load_binary(path)
    systems = merge_split_systems(omr.build_systems(ink))
    page = Image.open(path).convert("RGB")
    report = []
    for si, s in enumerate(systems):
        space = system_space(s)
        top = min(l[0] for l in s["lines"])
        bot = max(l[4] for l in s["lines"])
        pad = int(space * 6)
        y0b, y1b = max(0, top - pad), min(ink.shape[0], bot + pad)
        H, hn = heads.find_heads(ink, space, y0b, y1b)
        H = [h for h in H if -6.5 <= omr.step_at(s, h["x"], h["y"]) <= 12.5]
        bars = heads.find_barlines(hn, s, space, [h["x"] for h in H])
        acc = find_accidentals(hn, space, int(top - space * 2.5), int(bot + space * 2.5))
        if H:
            first = min(h["x"] for h in H)
            # the key signature is exactly two sharps here; anything further
            # right (the time signature) must not push the bound past bar 1
            keysig = sorted([a for a in acc if a["x"] < first - space * 3],
                            key=lambda a: a["x"])[:2]
            left_ok = (max(a["x1"] for a in keysig) + space * 1.2
                       if keysig else first - space * 8)
            acc = [a for a in acc if a["x"] > first - space * 3]
            H = [h for h in H if h["x"] > 150]
            # add half/whole notes, skipping the counters inside sharps and
            # naturals and anything already found as a filled head
            for c in heads.find_hollow(ink, space, y0b, y1b):
                st = omr.step_at(s, c["x"], c["y"])
                if not (-3.5 <= st <= 11.5) or c["x"] < left_ok:
                    continue
                if any(a["x0"] - 3 <= c["x"] <= a["x1"] + 3 and
                       a["y0"] - 3 <= c["y"] <= a["y1"] + 3 for a in acc):
                    continue
                if any(abs(h2["x"] - c["x"]) < space * 1.1 and
                       abs(h2["y"] - c["y"]) < space * 1.1 for h2 in H):
                    continue
                H.append(c)
            H.sort(key=lambda d: d["x"])

        # assign notes to measures
        edges = [0] + sorted(bars) + [ink.shape[1]]
        measures = [[] for _ in range(len(edges) - 1)]
        for h in H:
            k = int(np.searchsorted(edges, h["x"]) - 1)
            st = omr.step_at(s, h["x"], h["y"])
            l, o = heads.step_to_note(st)
            near = [a for a in acc if 0 < h["x"] - a["x"] < space * 2.4
                    and abs(a["y"] - h["y"]) < space * 1.6]
            sym = {"sharp": "#", "natural": "n", "flat": "b", "?": "?"}
            mark = sym[near[-1]["kind"]] if near else ""
            measures[k].append({"x": h["x"], "step": st, "name": f"{l}{o}",
                                "acc": mark})
        measures = [m for m in measures if m]

        # ---- overlay ----
        x0 = max(0, int(min([h["x"] for h in H], default=200) - space * 9))
        im = page.crop((x0, y0b, page.size[0], y1b))
        d = ImageDraw.Draw(im)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 21)
        except Exception:
            font = ImageFont.load_default()
        # draw the detected staff lines themselves, so the pitch geometry is
        # visually checkable rather than taken on trust
        for k, col in enumerate([(0, 200, 0), (0, 160, 220), (0, 200, 0),
                                 (0, 160, 220), (0, 200, 0)]):
            pts = [(x - x0, omr.line_y(s, float(x), k) - y0b)
                   for x in range(x0, page.size[0], 20)]
            d.line(pts, fill=col, width=2)
        for b in bars:
            d.line([(b - x0, 0), (b - x0, im.size[1])], fill=(0, 140, 255), width=4)
        for j, a in enumerate(acc):
            d.rectangle([a["x0"] - x0 - 2, a["y0"] - y0b - 2,
                         a["x1"] - x0 + 2, a["y1"] - y0b + 2],
                        outline=(255, 130, 0), width=4)
            d.text((a["x0"] - x0 - 4, a["y0"] - y0b - 30),
                   {"sharp": "#", "natural": "n", "flat": "b"}.get(a["kind"], "?"),
                   fill=(255, 110, 0), font=font)
        for h in H:
            st = omr.step_at(s, h["x"], h["y"])
            l, o = heads.step_to_note(st)
            x, y = h["x"] - x0, h["y"] - y0b
            bad = abs(st - round(st)) > 0.28
            d.ellipse([x - 16, y - 14, x + 16, y + 14],
                      outline=(255, 0, 0) if not bad else (200, 0, 200), width=4)
            d.text((x - 14, y - 46), f"{l}{o}", fill=(190, 0, 0), font=font)
        name = paths.OVERLAYS / f"ov_{tag}_{si:02d}.png"
        im.save(name)

        # flag horizontal gaps wide enough to hide an undetected half/whole note
        gaps = []
        xs_all = sorted(h["x"] for h in H)
        for a, b in zip(xs_all, xs_all[1:]):
            if b - a > space * 5.2 and not any(a < bl < b for bl in bars):
                gaps.append(int((a + b) / 2))
        txt = " | ".join(" ".join(n["name"] + n["acc"] for n in m)
                         for m in measures)
        if gaps:
            txt += f"\n    GAPS (check for half/whole notes) at x={gaps}"
        report.append(f"--- {tag} system {si}: {len(H)} heads, {len(bars)} bars, "
                      f"{len(acc)} accidentals -> {name.name}\n{txt}")
    print("\n\n".join(report))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 extract.py <p1|p2|path-to-image> [tag]")
    key = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else key
    paths.ensure_dirs()
    run(str(paths.page(key)), tag)

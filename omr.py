"""Staff detection + notehead extraction for the Corra e Olhe o Ceu photos.

The pages are phone photos: mild perspective + curl, so staff lines drift several
pixels across the width. Everything here works in narrow vertical strips so the
lines are locally straight, then interpolates between strip centres.
"""
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

STRIP = 120


def load_binary(path, block=41, offset=0.05):
    im = Image.open(path).convert("L")
    a = np.asarray(im, dtype=np.float32) / 255.0
    bg = ndi.uniform_filter(a, size=block)
    ink = (a < (bg - offset))
    return ink, a


def strip_peaks(ink, x0, x1, frac=0.55):
    """Row indices inside [x0,x1) that look like staff line segments."""
    sub = ink[:, x0:x1]
    prof = sub.sum(axis=1).astype(np.float32)
    w = x1 - x0
    hot = prof > w * frac
    peaks = []
    y = 0
    h = len(prof)
    while y < h:
        if hot[y]:
            y2 = y
            while y2 + 1 < h and hot[y2 + 1]:
                y2 += 1
            if y2 - y < 12:                     # a staff line is thin
                seg = prof[y:y2 + 1]
                peaks.append(y + int(np.argmax(seg)))
            y = y2 + 1
        else:
            y += 1
    return peaks


def group_staves(peaks, tol=0.30):
    """Consecutive peaks with near-equal spacing, in runs of exactly 5."""
    out = []
    i = 0
    while i + 4 < len(peaks):
        five = peaks[i:i + 5]
        d = np.diff(five)
        if d.min() > 8 and d.max() < 60 and (d.max() - d.min()) <= tol * d.mean():
            out.append(five)
            i += 5
        else:
            i += 1
    return out


def build_systems(ink):
    """-> list of systems; each is dict(xs=[...], lines=[[5 y] per strip])."""
    h, w = ink.shape
    xs, per_strip = [], []
    for x0 in range(0, w - STRIP, STRIP):
        pk = strip_peaks(ink, x0, x0 + STRIP)
        st = group_staves(pk)
        if st:
            xs.append(x0 + STRIP // 2)
            per_strip.append(st)

    # cluster staves across strips by their centre y
    systems = []
    for x, staves in zip(xs, per_strip):
        for s in staves:
            c = float(np.mean(s))
            hit = None
            for sysm in systems:
                if abs(sysm["c"] - c) < 40:
                    hit = sysm
                    break
            if hit is None:
                systems.append({"c": c, "xs": [x], "lines": [s]})
            else:
                hit["xs"].append(x)
                hit["lines"].append(s)
                hit["c"] = float(np.mean([np.mean(l) for l in hit["lines"]]))
    systems = [s for s in systems if len(s["xs"]) >= 6]
    systems.sort(key=lambda s: s["c"])

    # a strip that locked onto beams instead of staff lines shows an off
    # spacing; drop those before fitting so they cannot bend the geometry
    allsp = np.array([np.diff(l).mean() for s in systems for l in s["lines"]])
    med = float(np.median(allsp))
    for s in systems:
        keep = [(x, l) for x, l in zip(s["xs"], s["lines"])
                if abs(np.diff(l).mean() - med) < med * 0.14]
        if len(keep) >= 5:
            s["xs"] = [k[0] for k in keep]
            s["lines"] = [k[1] for k in keep]
        fit_system(s)
    return systems


def _robust_polyfit(x, y, deg, iters=4, keep=2.2):
    """Least squares with iterative outlier rejection."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = np.ones(len(x), bool)
    c = np.polyfit(x[m], y[m], deg)
    for _ in range(iters):
        r = np.abs(y - np.polyval(c, x))
        s = np.median(r[m]) + 1e-6
        nm = r < max(keep * s, 1.2)
        if nm.sum() < deg + 2 or (nm == m).all():
            break
        m = nm
        c = np.polyfit(x[m], y[m], deg)
    return c, m


def fit_system(sysm):
    """Model the staff as centre(x) + (k-2)*spacing(x).

    Fitting the centre and the spacing separately, each with outlier rejection,
    keeps a strip that latched onto a beam instead of a staff line from bending
    the geometry -- which silently shifts every pitch in that bar.
    """
    xs = np.array(sysm["xs"], dtype=float)
    lines = np.array(sysm["lines"], dtype=float)          # (nstrip, 5)
    centre = lines.mean(axis=1)
    spacing = np.diff(lines, axis=1).mean(axis=1)
    deg = 2 if len(xs) >= 6 else 1
    cc, mc = _robust_polyfit(xs, centre, deg)
    sc, ms = _robust_polyfit(xs, spacing, 1)
    sysm["fit"] = {"centre": cc, "spacing": sc,
                   "kept": int(mc.sum()), "n": len(xs)}
    return sysm


def line_y(sysm, x, k):
    """y of staff line k (0 = top line) at column x."""
    f = sysm.get("fit")
    if f is None:
        f = fit_system(sysm)["fit"]
    return float(np.polyval(f["centre"], x) + (k - 2) * np.polyval(f["spacing"], x))


def step_at(sysm, x, y):
    """Diatonic steps above the bottom line (E4 in treble). 1 step = half a space."""
    y0 = line_y(sysm, x, 4)        # bottom line
    y4 = line_y(sysm, x, 0)        # top line
    space = (y0 - y4) / 8.0        # half-space = one diatonic step
    return (y0 - y) / space


if __name__ == "__main__":
    import sys
    ink, gray = load_binary(sys.argv[1])
    systems = build_systems(ink)
    print(f"{len(systems)} systems")
    for n, s in enumerate(systems):
        top = np.mean([l[0] for l in s["lines"]])
        bot = np.mean([l[4] for l in s["lines"]])
        print(f"  sys {n:2d}  y~{top:6.0f}..{bot:6.0f}  strips={len(s['xs']):3d}  "
              f"x={min(s['xs'])}..{max(s['xs'])}  spacing={(bot-top)/4:.1f}")

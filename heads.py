"""Notehead + barline extraction, building on omr.build_systems."""
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
import omr


def vertical_run_filter(ink, max_run):
    """Drop ink pixels whose vertical ink-run is <= max_run (kills staff lines)."""
    h, w = ink.shape
    out = np.zeros_like(ink)
    a = ink.astype(np.int8)
    # run length per column via cumulative trick
    for x in range(w):
        col = a[:, x]
        if not col.any():
            continue
        idx = np.flatnonzero(np.diff(np.concatenate(([0], col, [0]))))
        starts, ends = idx[0::2], idx[1::2]
        keep = (ends - starts) > max_run
        for s, e in zip(starts[keep], ends[keep]):
            out[s:e, x] = True
    return out


def disk(r):
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def find_heads(ink, space, ymin, ymax):
    # Work on the cropped band, not a page-sized array with a band pasted into
    # it: the morphology below costs the whole page otherwise, once per system.
    ymin = max(0, int(ymin))
    ymax = min(ink.shape[0], int(ymax))
    band = ink[ymin:ymax]
    # 1. remove staff lines (thin horizontal structures)
    noline_c = vertical_run_filter(band, max_run=int(space * 0.35))
    noline = np.zeros_like(ink)
    noline[ymin:ymax] = noline_c
    # 2. fill any enclosed counters. No morphological closing here: it welds
    #    adjacent 16th-note heads into one blob and loses both.
    filled = ndi.binary_fill_holes(noline_c)
    # 3. keep only blobs that can hold a notehead-sized disk
    r = max(3, int(round(space * 0.34)))
    opened = ndi.binary_opening(filled, structure=disk(r))
    lab, n = ndi.label(opened)
    heads = []
    for sl, i in zip(ndi.find_objects(lab), range(1, n + 1)):
        ys, xs = sl
        hgt, wid = ys.stop - ys.start, xs.stop - xs.start
        area = (lab[sl] == i).sum()
        # a printed notehead is a tilted ellipse: reliably wider than tall,
        # ~1.3 space wide x ~1.05 space high. Sharps are taller than wide,
        # chord letters and augmentation dots are too small.
        if not (space * 0.90 <= wid <= space * 1.62):
            continue
        if not (space * 0.82 <= hgt <= space * 1.28):
            continue
        if wid < hgt * 0.95:
            continue
        if not (space * space * 0.62 <= area <= space * space * 1.35):
            continue
        # centre of mass within the blob's own box; `lab == i` over the whole
        # array would rebuild a page-sized mask for every notehead
        cy, cx = ndi.center_of_mass(lab[sl] == i)
        heads.append({"x": float(cx) + xs.start, "y": float(cy) + ys.start + ymin,
                      "w": wid, "h": hgt, "area": int(area)})
    heads.sort(key=lambda d: d["x"])
    return heads, noline


def find_hollow(ink, space, ymin, ymax):
    """Half/whole notes, found by their enclosed white counter.

    These survive neither the opening (too thin a ring) nor a fill after the
    staff lines are stripped (the strip cuts the ring open), so look for the
    counter in the untouched image, where the ring is still closed.
    A notehead counter is markedly flatter than the cells the staff lines and
    stems enclose, which is what separates the two.
    """
    ymin = max(0, int(ymin))
    ymax = min(ink.shape[0], int(ymax))
    band = ink[ymin:ymax]
    holes = ndi.binary_fill_holes(band) & ~band
    lab, n = ndi.label(holes)
    out = []
    for sl, i in zip(ndi.find_objects(lab), range(1, n + 1)):
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        if not (space * 0.25 <= h <= space * 0.62):
            continue
        if not (space * 0.35 <= w <= space * 0.95):
            continue
        if w <= h * 1.05:
            continue
        area = (lab[sl] == i).sum()
        if area < 0.5 * w * h:
            continue
        # A notehead counter is ringed by ink on every side. The cells that
        # stems and beams enclose are bounded by thin strokes left and right,
        # so their surrounding annulus is mostly white -- that is the test.
        pad = max(2, int(round(space * 0.22)))
        y0, y1 = max(0, ys.start - pad), min(band.shape[0], ys.stop + pad)
        x0, x1 = max(0, xs.start - pad), min(band.shape[1], xs.stop + pad)
        win = band[y0:y1, x0:x1]
        holewin = (lab[y0:y1, x0:x1] == i)
        annulus = win.sum()
        denom = win.size - holewin.sum()
        if denom <= 0 or annulus / denom < 0.78:
            continue
        cy, cx = ndi.center_of_mass(lab[sl] == i)
        out.append({"x": float(cx) + xs.start, "y": float(cy) + ys.start + ymin,
                    "w": w, "h": h, "area": int(area), "hollow": True})
    out.sort(key=lambda d: d["x"])
    return out


def staff_free(ink, sysm, pad=3):
    """Copy of the image with only the five fitted staff lines erased.

    The vertical-run filter used for noteheads cannot be reused for ties: a tie
    is a thin, near-horizontal stroke, so that filter deletes it along with the
    staff lines. Masking the fitted lines instead leaves ties intact.
    """
    out = ink.copy()
    xs = np.arange(ink.shape[1])
    for k in range(5):
        ys = omr.line_y(sysm, xs.astype(float), k)
        for dy in range(-pad, pad + 1):
            rows = np.clip((ys + dy).astype(int), 0, ink.shape[0] - 1)
            out[rows, xs] = False
    return out


def has_tie(free, space, a, b, min_cover=0.85, max_thick=0.40):
    """Is a tie arc drawn between noteheads a and b?

    Looks just outside the pair, above and below, for a stroke that spans the
    whole gap and stays thin. A beam also spans the gap but is about twice as
    thick, which is what separates the two.
    """
    x0 = int(a["x"] + space * 0.5)
    x1 = int(b["x"] - space * 0.5)
    if x1 - x0 < space * 0.25:
        return False
    ymid = (a["y"] + b["y"]) / 2.0
    for sign in (-1, 1):
        lo = int(ymid + sign * space * 1.9)
        hi = int(ymid + sign * space * 0.25)
        lo, hi = min(lo, hi), max(lo, hi)
        band = free[lo:hi, x0:x1]
        if band.size == 0:
            continue
        cols = band.any(axis=0)
        if cols.mean() < min_cover:
            continue
        if band.sum(axis=0)[cols].mean() <= space * max_thick:
            return True
    return False


def find_barlines(noline, sysm, space, head_xs=()):
    """Thin vertical strokes spanning the whole staff height.

    A stem can also span the staff, so drop candidates sitting next to a
    notehead -- a barline stands alone.
    """
    top = min(l[0] for l in sysm["lines"])
    bot = max(l[4] for l in sysm["lines"])
    band = noline[top:bot + 1]
    colsum = band.sum(axis=0)
    # the staff tilts, so the true height at column x is less than bot-top;
    # measure it per column instead of using the global extent.
    w = noline.shape[1]
    xs = np.arange(w)
    hgt = np.array([omr.line_y(sysm, float(x), 4) - omr.line_y(sysm, float(x), 0)
                    for x in xs])
    # A barline runs the full staff height and stops there. A stem also spans
    # the staff but continues out to its beam. Measure that on the contiguous
    # stroke through the staff -- looking at all ink in the column instead lets
    # an unrelated chord symbol or slur overhead disqualify a real barline.
    allx = np.arange(w, dtype=float)
    tops = omr.line_y(sysm, allx, 0)
    bots = omr.line_y(sysm, allx, 4)
    span = bots - tops
    lo = int(max(0, tops.min() - space * 2))
    hi = int(min(noline.shape[0], bots.max() + space * 2))
    sub = noline[lo:hi]
    hot = np.zeros(w, bool)
    for x in np.flatnonzero(sub.sum(axis=0) >= 0.90 * span):
        col = sub[:, x]
        idx = np.flatnonzero(np.diff(np.concatenate(([0], col.view(np.int8), [0]))))
        t, b = tops[x] - lo, bots[x] - lo
        for a, e in zip(idx[0::2], idx[1::2]):
            if (min(e, b) - max(a, t)) >= 0.90 * span[x] and \
               (t - a) <= 0.60 * space and (e - b) <= 0.60 * space:
                hot[x] = True
                break
    bars, x = [], 0
    while x < len(hot):
        if hot[x]:
            x2 = x
            while x2 + 1 < len(hot) and hot[x2 + 1]:
                x2 += 1
            if x2 - x < space * 0.8:
                bars.append((x + x2) / 2.0)
            x = x2 + 1
        else:
            x += 1
    if len(head_xs):
        hx = np.asarray(head_xs, dtype=float)
        bars = [b for b in bars if np.abs(hx - b).min() > space * 0.75]
    # collapse double barlines / repeat marks into one
    merged = []
    for b in bars:
        if merged and b - merged[-1] < space * 1.2:
            merged[-1] = (merged[-1] + b) / 2.0
        else:
            merged.append(b)
    return merged


LETTERS = "EFGABCD"        # step 0 = E4 (bottom line of treble staff)
BASE_MIDI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def step_to_note(step):
    """step 0 = E4. Returns (letter, octave)."""
    k = int(round(step))
    letter = LETTERS[k % 7]
    octave = 4 + (k + 2) // 7          # E4 is step 0; C5 is step 5
    return letter, octave


def analyse(path, sysm_index=None, page=""):
    ink, gray = omr.load_binary(path)
    systems = omr.build_systems(ink)
    return ink, systems

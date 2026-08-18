"""Full transcription: pages -> pitches -> 12-hole C chromatic harmonica tab."""
import sys, json
import numpy as np
from scipy import ndimage as ndi
import omr, heads, extract, paths

SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# Key signatures are written in a fixed order at fixed staff positions, so the
# glyphs before the first note identify the key. Steps count diatonic degrees
# from E4 (the bottom line of a treble staff).
SHARP_ORDER, SHARP_STEPS = "FCGDAEB", [8, 5, 9, 6, 3, 7, 4]
FLAT_ORDER, FLAT_STEPS = "BEADGCF", [4, 7, 3, 6, 2, 5, 1]
KEYSIG_FALLBACK = {"F": 1, "C": 1}            # D major, if detection fails


def detect_keysig(acc_all, first_note_x, sysm, space):
    """Read the key signature from the accidentals before the first note.

    Matching the glyphs against the expected sequence, in order, is what keeps
    the clef and the time signature out: a stray glyph fails the position test
    and stops the run rather than being counted.
    """
    glyphs = sorted((a for a in acc_all
                     if a["x"] < first_note_x - space * 3
                     and a["kind"] in ("sharp", "flat")),
                    key=lambda a: a["x"])
    if not glyphs:
        return {}, None
    kinds = [g["kind"] for g in glyphs]
    kind = max(set(kinds), key=kinds.count)
    order = SHARP_ORDER if kind == "sharp" else FLAT_ORDER
    steps = SHARP_STEPS if kind == "sharp" else FLAT_STEPS
    n = 0
    for i, g in enumerate(glyphs):
        if i >= len(steps) or g["kind"] != kind:
            break
        if abs(omr.step_at(sysm, g["x"], g["y"]) - steps[i]) > 0.6:
            break
        n = i + 1
    alt = 1 if kind == "sharp" else -1
    return {order[i]: alt for i in range(n)}, (kind, n)
BLOW, DRAW = [0, 4, 7, 12], [2, 5, 9, 11]
BASE, HOLES = 60, 12                          # 12-hole chromatic in C


def pitch_at(hole, draw, slide):
    g, i = (hole - 1) // 4, (hole - 1) % 4
    return BASE + 12 * g + (DRAW[i] if draw else BLOW[i]) + (1 if slide else 0)


def tab_for(midi, prev_hole):
    cands = [(h, d, s) for h in range(1, HOLES + 1) for d in (0, 1) for s in (0, 1)
             if pitch_at(h, d, s) == midi]
    if not cands:
        return None
    cands.sort(key=lambda c: (c[2],
                              abs(c[0] - prev_hole) if prev_hole else c[0],
                              c[0]))
    h, d, s = cands[0]
    return {"hole": h, "draw": bool(d), "slide": bool(s),
            "txt": ("-" if d else "") + str(h) + ("*" if s else "")}


def find_grace(ink, space, ymin, ymax, noline):
    """Small ornament noteheads -- about two thirds the size of a normal head."""
    filled = ndi.binary_fill_holes(noline)
    op = ndi.binary_opening(filled, structure=heads.disk(max(2, int(round(space * 0.22)))))
    lab, n = ndi.label(op)
    out = []
    for sl, i in zip(ndi.find_objects(lab), range(1, n + 1)):
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        a = (lab[sl] == i).sum()
        if not (space * 0.52 <= w <= space * 0.92):
            continue
        if not (space * 0.46 <= h <= space * 0.86):
            continue
        if w < h * 0.95:
            continue
        if not (space * space * 0.22 <= a <= space * space * 0.66):
            continue
        cy, cx = ndi.center_of_mass(lab == i)
        out.append({"x": float(cx), "y": float(cy), "grace": True})
    return out


# printed rehearsal numbers on the two pages -- these pin the bar count of
# every system, so a wrong barline count shows up instead of silently
# renumbering everything after it
P1_STARTS = [1, 7, 13, 19, 25, 32, 39, 46, 52, 58, 63]
P2_STARTS = [63, 69, 76, 82, 89, 96, 102, 108, 115, 122, 128, 135]

# Optional, per page key. Without a table the bars are simply numbered in
# order; the trade is that nothing cross-checks the barline detector, so no
# system can be marked "checked".
STARTS = {"p1": P1_STARTS, "p2": P2_STARTS}


def transcribe(path, tag, starts=None, first_bar=1):
    ink, gray = omr.load_binary(path)
    systems = extract.merge_split_systems(omr.build_systems(ink))
    bars_out, problems, votes = [], [], []
    barno = first_bar
    for si, s in enumerate(systems):
        if starts and si + 1 < len(starts):
            want, sys_start = starts[si + 1] - starts[si], starts[si]
        else:
            want, sys_start = None, (starts[si] if starts else barno)
        space = extract.system_space(s)
        top = min(l[0] for l in s["lines"])
        bot = max(l[4] for l in s["lines"])
        pad = int(space * 6)
        y0b, y1b = max(0, top - pad), min(ink.shape[0], bot + pad)
        H, hn = heads.find_heads(ink, space, y0b, y1b)
        H = [h for h in H if -6.5 <= omr.step_at(s, h["x"], h["y"]) <= 12.5
             and h["x"] > 150]
        omr.calibrate(s, H)
        if not H:
            for k in range(want or 1):
                # tag rest-only systems too, or they all group together as one
                bars_out.append({"bar": sys_start + k, "notes": [], "rest": True,
                                 "sys": f"{tag}{si}", "ok": want is not None})
            barno = sys_start + (want or 1)
            continue
        first = min(h["x"] for h in H)
        acc_all = extract.find_accidentals(hn, space, int(top - space * 2.5),
                                           int(bot + space * 2.5))
        keysig = sorted([a for a in acc_all if a["x"] < first - space * 3],
                        key=lambda a: a["x"])[:2]
        left_ok = (max(a["x1"] for a in keysig) + space * 1.2
                   if keysig else first - space * 8)
        acc = [a for a in acc_all if a["x"] > first - space * 3]
        _, vote = detect_keysig(acc_all, first, s, space)
        if vote:
            votes.append(vote)
        for c in heads.find_hollow(ink, space, y0b, y1b):
            st = omr.step_at(s, c["x"], c["y"])
            if not (-3.5 <= st <= 11.5) or c["x"] < left_ok:
                continue
            if any(a["x0"] - 3 <= c["x"] <= a["x1"] + 3 and
                   a["y0"] - 3 <= c["y"] <= a["y1"] + 3 for a in acc_all):
                continue
            if any(abs(h2["x"] - c["x"]) < space * 1.1 and
                   abs(h2["y"] - c["y"]) < space * 1.1 for h2 in H):
                continue
            H.append(c)
        for c in []:   # grace pass disabled: too many false positives
            st = omr.step_at(s, c["x"], c["y"])
            if not (-3.5 <= st <= 13.5) or c["x"] < left_ok:
                continue
            if any(a["x0"] - 3 <= c["x"] <= a["x1"] + 3 and
                   a["y0"] - 3 <= c["y"] <= a["y1"] + 3 for a in acc_all):
                continue
            if any(abs(h2["x"] - c["x"]) < space * 0.9 and
                   abs(h2["y"] - c["y"]) < space * 0.9 for h2 in H):
                continue
            H.append(c)
        H.sort(key=lambda d: d["x"])
        bars = heads.find_barlines(hn, s, space,
                                   [h["x"] for h in H if not h.get("grace")])
        # count the regions the barlines cut the staff into, not just the ones
        # holding notes -- a rest-only measure is still a measure
        sx0, sx1 = left_ok - space, max(s["xs"]) + omr.STRIP
        edges = [sx0] + sorted(b for b in bars if sx0 < b < sx1) + [sx1]
        buckets = [[] for _ in range(len(edges) - 1)]
        for h in H:
            k = int(np.clip(np.searchsorted(edges, h["x"]) - 1,
                            0, len(buckets) - 1))
            buckets[k].append(h)
        # a closing barline leaves an empty region past the end of the music
        while len(buckets) > 1 and not buckets[-1]:
            buckets.pop()
        while len(buckets) > 1 and not buckets[0]:
            buckets.pop(0)
        if want is not None and len(buckets) != want:
            problems.append(f"{tag} system {si} (bars {sys_start}-"
                            f"{sys_start + want - 1}): found {len(buckets)} "
                            f"measures, expected {want}")
        # Pitch every head in one pass over the system, then look for ties.
        # Ties routinely cross a barline, so this cannot be done per measure.
        def spelling_of(h):
            st = omr.step_at(s, h["x"], h["y"])
            letter, octv = heads.step_to_note(st)
            off = abs(st - round(st))
            # an accidental sits immediately left, on the same line or space
            near = [a for a in acc
                    if 0 < h["x"] - a["x"] < space * 2.4
                    and abs(a["y"] - h["y"]) < space * 0.62]
            # Apply an accidental only to the note it touches. It should
            # carry to the rest of the bar, but one misread glyph would then
            # poison every later note on that pitch, and this engraving
            # restates accidentals anyway.
            alt = ({"sharp": 1, "flat": -1, "natural": 0}.get(near[-1]["kind"], 0)
                   if near else None)
            return letter, octv, alt, off

        free = heads.staff_free(ink, s)
        info = {}
        prev_h = prev_midi = None
        for h in sorted(H, key=lambda d: d["x"]):
            spell = spelling_of(h)
            off = spell[3]
            spell = spell[:3]
            # Comparing the spelling rather than a pitch number lets this run
            # before the key signature is known, and still tells F# from
            # F-natural, because an explicit accidental is part of the spelling.
            tied = (prev_h is not None and spell == prev_midi
                    and heads.has_tie(free, space, prev_h, h))
            info[id(h)] = {"spell": spell, "tied": tied, "off": off}
            prev_h, prev_midi = h, spell

        for j, b in enumerate(buckets):
            barno = sys_start + j
            notes = []
            for h in b:
                d = info[id(h)]
                letter, octv, alt = d["spell"]
                notes.append({"letter": letter, "octave": octv, "alt": alt,
                              "off": round(d["off"], 3),
                              "grace": bool(h.get("grace")),
                              "hollow": bool(h.get("hollow")),
                              "explicit": alt is not None,
                              "tied": d["tied"]})
            bars_out.append({"bar": barno, "notes": notes,
                             "sys": f"{tag}{si}",
                             "ok": want is not None and len(buckets) == want})
        barno = sys_start + len(buckets)
    return bars_out, problems, votes


SECTIONS = [(1, 18, "Intro"), (19, 31, "A1 - solo"), (32, 62, "Instrumental"),
            (63, 81, "Instrumental (cont.)"), (82, 95, "A2 - solo"),
            (96, 114, "A2"), (115, 134, "Coda")]


def section_of(bar):
    for lo, hi, name in SECTIONS:
        if lo <= bar <= hi:
            return name
    return ""


# A notehead sits on a line or in a space. One landing a good fraction of a
# step away is the reading the eye should check first -- that is where a pitch
# gets rounded onto the wrong line.
SUSPECT = 0.22


def by_system(all_bars):
    """Group bars back into the staff lines they were printed on.

    The note order within a system is reliable; the barline positions are not
    always, so the printed system is the unit worth publishing against.
    """
    rows, prev_hole = [], None
    groups = {}
    for bar in all_bars:
        groups.setdefault(bar.get("sys", "rest"), []).append(bar)
    for key, g in groups.items():
        meas_tab, meas_notes = [], []
        flags, idx = [], 0
        for bar in g:
            if meas_tab:
                idx += 1            # the "|" separator occupies a slot
            toks, names = [], []
            for n in bar["notes"]:
                if n.get("tied"):
                    continue            # held over from the previous note
                t = tab_for(n["midi"], prev_hole)
                if t:
                    prev_hole = t["hole"]
                toks.append(t["txt"] if t else "X")
                names.append(n["name"])
                if n.get("off", 0) >= SUSPECT or not t:
                    flags.append(idx)
                idx += 1
            if not toks and bar["notes"]:
                toks = names = ["~"]    # whole measure is a held note
                idx += 1
            if not toks:
                idx += 1
            meas_tab.append(" ".join(toks) if toks else "-")
            meas_notes.append(" ".join(names) if names else "-")
        rows.append({"lo": g[0]["bar"], "hi": g[-1]["bar"],
                     "section": section_of(g[0]["bar"]),
                     "ok": all(b.get("ok", True) for b in g),
                     "tab": " | ".join(meas_tab),
                     "notes": " | ".join(meas_notes),
                     "check": flags})
    return rows


def resolve_keysig(votes):
    """Settle the key signature for the whole piece from per-system readings.

    A key signature is constant across a piece, and the position test makes a
    false reading unlikely while a missed glyph is common -- the sharps sit on
    the staff lines and often fragment when those are removed. So take the
    longest run seen anywhere rather than a majority, which would be dragged
    down by the systems that under-read.
    """
    if not votes:
        return dict(KEYSIG_FALLBACK), "fallback (D major)"
    kinds = [k for k, _ in votes]
    kind = max(set(kinds), key=kinds.count)
    n = max((c for k, c in votes if k == kind), default=0)
    if n == 0:
        return {}, "none (C major)"
    order = SHARP_ORDER if kind == "sharp" else FLAT_ORDER
    alt = 1 if kind == "sharp" else -1
    sig = {order[i]: alt for i in range(n)}
    names = "".join(order[i] + ("#" if alt > 0 else "b") for i in range(n))
    return sig, f"{n} {kind}{'s' if n != 1 else ''} ({names})"


def apply_keysig(all_bars, keysig):
    for bar in all_bars:
        for n in bar["notes"]:
            alt = n["alt"] if n["alt"] is not None else keysig.get(n["letter"], 0)
            midi = 12 * (n["octave"] + 1) + PC[n["letter"]] + alt
            n["midi"] = midi
            n["name"] = SHARP[midi % 12] + str(midi // 12 - 1)


def main():
    paths.ensure_dirs()
    keys = sorted((k for k in paths.PAGES if k.startswith("p") and k[1:].isdigit()),
                  key=lambda k: int(k[1:]))
    all_bars, problems, votes, nextbar = [], [], [], 1
    for k in keys:
        bars, probs, v = transcribe(str(paths.PAGES[k]), k, STARTS.get(k), nextbar)
        all_bars += bars
        problems += probs
        votes += v
        nextbar = (bars[-1]["bar"] + 1) if bars else nextbar
    keysig, ksdesc = resolve_keysig(votes)
    apply_keysig(all_bars, keysig)
    print(f"key signature read from the page: {ksdesc}")

    rows = by_system(all_bars)
    json.dump(rows, open(paths.OUT / "systems.json", "w"), indent=1)
    json.dump(all_bars, open(paths.OUT / "bars.json", "w"))

    lines = ["Corra e Olhe o Ceu - Cartola / Dalmo Castello (1974)",
             "12-hole C chromatic harmonica.  5=blow 5  |  -5=draw 5  |  *=slide in",
             "'checked' = measure count reconciles with the printed bar numbers",
             ""]
    for r in rows:
        flag = "checked" if r["ok"] else "draft  "
        lines.append(f"bars {r['lo']:>3}-{r['hi']:<3} [{flag}]  {r['tab']}")
    lines += ["", "Note names (same order):", ""]
    for r in rows:
        lines.append(f"bars {r['lo']:>3}-{r['hi']:<3}  {r['notes']}")
    (paths.OUT / "tab.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for p in problems:
        print("!! " + p)
    ties = sum(1 for b in all_bars for n in b["notes"] if n.get("tied"))
    heads_total = sum(len(b["notes"]) for b in all_bars)
    shaky = sum(1 for b in all_bars for n in b["notes"] if n.get("off", 0) >= SUSPECT)
    print(f"\n{ties} ties collapsed out of {heads_total} noteheads")
    print(f"{shaky} noteheads sit more than {SUSPECT} of a step off a staff "
          f"position -- flagged for checking")
    ok = sum(1 for r in rows if r["ok"])
    print(f"\n{ok}/{len(rows)} systems reconcile with the printed bar numbers")
    for r in rows:
        print(("OK " if r["ok"] else "?? ") + f"{r['lo']:>3}-{r['hi']:<3}: {r['tab']}")
    print(f"\nwrote {paths.OUT}/systems.json, bars.json, tab.txt")


if __name__ == "__main__":
    main()

# harmonica-transcriber

Reads a photo of printed sheet music and outputs chromatic harmonica tablature —
hole number, blow or draw, and slide in/out.

Built against *Corra e Olhe o Céu* (Cartola / Dalmo Castello, 1974), 134 bars in D,
for a **12-hole solo-tuned chromatic in C**.

## Run it

Drop a photo in and get tab back, in a browser:

```bash
pip install -r requirements.txt
python3 serve.py           # then open http://localhost:8000
```

The pipeline is numpy/scipy so it cannot run in a browser; `serve.py` serves
the page and does the work locally. Nothing leaves the machine. HEIC is
handled, and the page may be sideways -- the rotation is worked out by trying
each quarter turn and keeping whichever finds the most staves. About 5 seconds
for a full page.

**Every tab line is editable in place.** Click a line, retype it, Enter to
confirm or Esc to undo. An unknown token is flagged in red rather than
silently dropped, edited lines are badged and can be reverted individually,
and the PDF prints what is on screen. This is not a nicety: a photo read by
machine always needs a human pass, and the correction step is where the
output becomes trustworthy.

In the studio, **Save corrections** writes `out/tab-corrected.txt` and
`out/corrections.json`. On the published chart there is no server, so edits
are kept in the browser's local storage instead -- a rebuild regenerates the
table and would otherwise wipe them.

**Drop in every page of a piece at once.** They are sorted by filename, read
one at a time with progress shown, then joined. Joining is not concatenation:
the key signature is pooled across all pages, because pooling is what makes it
reliable, and bar numbers run straight through, so no page can be finished on
its own. That is why the API is `/api/reset`, `/api/page` per file, then
`/api/finish`. Both the studio and the published chart have a **Save as PDF**
button, which prints through the browser's own engine rather than bundling a
PDF library.

Or run the committed pages straight through:

```bash
python3 transcribe.py      # scans -> out/systems.json, out/bars.json, out/tab.txt
python3 build_web.py       # out/systems.json -> web/harp.html
```

Per-page inspection, which is how you check the machine's work:

```bash
python3 extract.py p1              # writes out/overlays/ov_p1_*.png
python3 inspect_system.py p2 5     # one system, with a step table printed
```

Overlays draw the **detected staff lines** in green/blue over the real ones,
every notehead it found in red, accidentals in orange boxes, and barlines in
blue. If the drawn lines drift off the printed lines, the pitches in that
region are wrong and nothing downstream can be trusted.

## How it works

Four stages, in `omr.py` → `heads.py` → `extract.py` → `transcribe.py`.

1. **Staff geometry** (`omr.py`). Staff lines are found in narrow vertical
   strips, so local tilt doesn't matter, then fitted as
   `centre(x) + (k-2) * spacing(x)` with iterative outlier rejection.
   The fit is the whole ballgame: an early version interpolated raw
   per-strip detections, and in dense bars a *beam* got picked up as a staff
   line and bent the geometry, silently shifting every pitch in that bar.
   Fitting centre and spacing separately, with outliers thrown out, is what
   makes it stable.

2. **Noteheads** (`heads.py`). Staff lines are removed by dropping ink whose
   vertical run is shorter than ~0.35 of a staff space, then a morphological
   opening keeps blobs that can hold a notehead-sized disk. The decisive
   filter is that a printed notehead is a tilted ellipse — reliably **wider
   than tall**. Sharps are taller than wide, chord letters and augmentation
   dots are too small.
   No morphological closing anywhere in this path: it welds adjacent
   16th-note heads into one blob and loses both.

3. **Half/whole notes** (`heads.find_hollow`). These are rings, so the opening
   misses them, and filling after the staff lines are stripped leaks (the
   strip cuts the ring open). They're found instead by their enclosed white
   counter in the *untouched* image, where the ring is still closed. A
   notehead counter is much flatter than the cells stems and beams enclose,
   and it's ringed by ink on all sides — that annulus test is what separates
   the two.

4. **Ties** (`heads.staff_free` / `heads.has_tie`). A tie means one sustained
   note, so the second head must not be played again — and since tab carries
   no duration, printing it twice reads as re-articulating. Same pitch is not
   enough to decide: this music genuinely repeats notes, so the arc itself is
   detected. It is looked for just outside the pair, above and below, as a
   stroke spanning the whole gap while staying thin; a beam spans the same gap
   but is about twice as thick. This runs on a copy of the image with only the
   fitted staff lines masked out — the vertical-run filter used for noteheads
   deletes ties, which are themselves thin near-horizontal strokes.
   Ties cross barlines, so it runs over the whole system, not per measure.

5. **Pitch and tab** (`transcribe.py`). Step position → letter + octave, then
   the key signature (F♯, C♯) unless an accidental glyph sits immediately left
   at the same staff position. Accidentals apply only to the note they touch,
   not to the rest of the bar: engraving convention says they should carry,
   but one misread glyph then poisons every later note on that pitch, and this
   chart restates accidentals anyway.
   Tab picks among enharmonic fingerings by preferring slide-out, then the
   hole nearest the previous note. A measure whose notes are all held over
   prints as `~`.

## Accuracy, honestly

Pitch extraction is good. Two independent checks:

- Bars 1–5 match the hole numbers pencilled on the original chart.
- The Coda restates the Intro, and extracted from a *different page* it
  produces identical tab — bars 2, 4, 6, 8, 10 all match token for token.
  Same for the A2 section against the first instrumental.

Bar alignment is weaker. `transcribe.py` checks each system's measure count
against the printed rehearsal numbers (`P1_STARTS` / `P2_STARTS`) and flags
mismatches. **8 of 21 systems reconcile**; the rest are marked `draft` — right
notes in the right order, but a barline may sit wrong. That's why output is
published per *system* rather than per bar.

### Known gaps

- **Grace notes are not detected.** There was a pass for them; it fired on
  beam fragments and flag tips and invented notes, so it's disabled
  (`find_grace` in `transcribe.py`, currently unreachable). Real ornaments in
  bars 7 and 69 are missing from the output.
- **Rhythm is not read at all** — only pitch, order and ties. Durations come from
  looking at the sheet.
- Two notes land outside the harp's range (`X` in the output, around bars 47
  and 114). Those are misreads.
- Accidental classification (sharp/natural/flat, from the vertical extent of
  the left and right strokes) is decent but not verified everywhere.

### Next things worth doing

1. Barline detection is still the highest-value fix — it's what gates the
   `draft` flags, and only 9 of 21 systems reconcile. A candidate must be a
   single contiguous stroke spanning the staff without overshooting it, and
   not sit beside a notehead. Sweeping those thresholds does not help; the
   remaining failures are not a threshold problem.
2. Beam/flag counting would give durations and make real rhythm possible.
3. Tie detection (arc between two same-pitch heads) would fix the doubles.
4. Beam/flag counting for durations, and repeat/D.S./Coda navigation.

## Another piece from the same book

The engraving is fixed, and every threshold is expressed in units of the
measured staff space, so nothing is tied to a particular resolution or to how
close the camera was. Adding a piece is: drop the deskewed pages in `scans/`
and run `transcribe.py`. Pages are discovered by filename order.

Read from the page automatically:

- **Staff geometry and scale** — per system, so pages at different sizes mix
  freely.
- **Key signature** — from the glyphs between the clef and the first note,
  matched against the fixed order and staff positions sharps and flats are
  written in. It is resolved per *piece*, not per system: a false reading is
  unlikely (a glyph must land within 0.6 of a step of the exact expected
  position, in sequence) while a missed one is common, because the signature
  sharps sit on the staff lines and fragment when those are removed. So the
  longest run seen anywhere wins rather than a majority.

Still piece-specific, all optional:

- `STARTS` in `transcribe.py` — the printed rehearsal numbers, used only to
  cross-check the barline detector. Without them everything still runs; bars
  are numbered in order and no system can be marked `checked`.
- `SECTIONS` — cosmetic grouping in the published table.
- `BASE`/`HOLES` — the target harmonica, not the music.

Not handled yet: mid-piece key changes, clefs other than treble, and repeat /
D.S. / Coda navigation (the tab is in printed order, not playing order).

## Photographing the pages

Every threshold scales with the **staff space** — the gap between two staff
lines — so that number is the one that matters. The current photos measure
**20–24px**, from 12MP shots of a full page.

What would actually help, in order:

1. **Flatten the page.** The staves in these photos bend up to **0.7 of a
   staff space** across the width. One diatonic step is *half* a staff space,
   so that is 1.4 steps of drift that the fit has to model rather than read.
   Weigh the spiral binding down, shoot straight down, keep the lens parallel
   to the paper. A phone "scan document" mode does perspective correction and
   is likely better than a raw photo.
2. **More pixels per staff.** At 20px per staff space, a sharp is about 20px
   wide, and sharp / natural / flat are told apart by where their strokes
   start and stop — a handful of pixels. That is the resolution-limited part,
   and it is exactly what misreads today. Shooting at the phone's maximum
   resolution (48MP rather than 12MP) roughly doubles the staff space and
   costs nothing.
3. **Flat, diffuse light, no flash.** Thresholding is local so a gradient is
   fine, but a specular highlight erases the print underneath it.
4. **Whole page, one page per shot**, including the left margin where the bar
   numbers are, and the key signature at the start of every system.
5. **Consistent upright orientation**, so no rotation has to be configured in
   `prepare.py`.

Not worth worrying about: even lighting across the page, mild tilt, moderate
JPEG noise, and the exact distance — all of that is already absorbed.

## Layout

```
paths.py            project paths; scripts run from any directory
prepare.py          HEIC photos -> deskewed page PNGs (macOS sips)
omr.py              staff line detection and fitting
heads.py            notehead, hollow-notehead and barline detection
extract.py          per-page extraction + verification overlays
inspect_system.py   one system, zoomed, with a step table
transcribe.py       pitches -> tab; writes out/
build_web.py        out/systems.json -> web/harp.html (idempotent)
serve.py            local upload UI; photo in, tab out, nothing uploaded
scans/              page1.png, page2.png, original/ (the HEIC photos)
out/                systems.json, bars.json, tab.txt, overlays/
web/harp.html       the full transcription + note chart, self-contained
web/studio.html     the upload front end that serve.py serves
```

`web/harp.html` is the published tab sheet: every system of the chart, plus a
reference table of every note the harmonica can reach. It is static except for
the note chart, which is generated at load.

Earlier revisions also carried an interactive converter (type a melody, get
tab, play it back). It was removed; `git log web/harp.html` has it if it is
ever wanted back. One trap it left behind, worth remembering: a literal
combining-mark range in a regex decoded wrong and threw at load, killing the
entire script — the page rendered but nothing worked. Keep anything the page
parses in **ASCII**.

## License and attribution

The **code** in this repository is MIT licensed (see `LICENSE`).

The **music is not mine and is not MIT licensed.** `scans/` contains
photographs of a printed chart, and `out/` plus the table in `web/harp.html`
contain a transcription derived from it:

> *Corra e Olhe o Céu* — Cartola / Dalmo Castello (1974)
> © CAPMUSIC Edições Ltda (Edições Euterpe Ltda). All rights reserved.
> International copyright secured.
> Chart from the Choro Música "C" book, revised by Italo Peron.

These are included only as the working example the pipeline was developed and
verified against. All rights remain with the copyright holders. If you
represent them and want this material removed, open an issue and I will take
it down.

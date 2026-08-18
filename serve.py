"""Local web front end: drop a photo in, get tablature out.

    python3 serve.py          then open http://localhost:8000

The pipeline is numpy/scipy, so it cannot run in a browser. This serves the
page and does the work locally instead -- nothing is uploaded anywhere.

The client posts raw file bytes with the name in a header rather than a
multipart form: the `cgi` module that used to parse multipart was removed in
Python 3.13, and this needs no parsing at all.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import numpy as np

import extract
import omr
import paths
import transcribe as T

PORT = 8000
SCOUT_PX = 2000     # orientation trials run at this size, not full resolution
HEIC = {".heic", ".heif"}


def to_png(src: Path) -> Path:
    """HEIC (or anything else) -> PNG, via macOS sips."""
    if src.suffix.lower() not in HEIC and src.suffix.lower() == ".png":
        return src
    dst = src.with_suffix(".png")
    subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(dst)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst


def orient(png: Path):
    """Find the rotation that actually yields staves.

    Photographing a book sideways is normal, so rather than making the user
    declare it, try each quarter turn and keep whichever finds most staves.
    The trials run on a downscaled copy -- deciding which way is up needs far
    less resolution than reading the notes, and at full size this was four
    passes over a 12MP image. Not too far down, though: staff detection needs
    the line spacing to stay above a few pixels, so this falls back to full
    resolution if the small copy finds nothing.
    """
    small = png.with_name(f"{png.stem}_small.png")
    shutil.copy(png, small)
    subprocess.run(["sips", "-Z", str(SCOUT_PX), str(small)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    best_rot, best_n = 0, -1
    for rot in (0, 90, 180, 270):
        trial = small
        if rot:
            trial = small.with_name(f"{small.stem}_r{rot}.png")
            shutil.copy(small, trial)
            subprocess.run(["sips", "-r", str(rot), str(trial)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ink, _ = omr.load_binary(str(trial))
            n = len(extract.merge_split_systems(omr.build_systems(ink)))
        except Exception:
            n = -1
        if n > best_n:
            best_rot, best_n = rot, n
    if best_n < 1:                      # too small to see staves; use the original
        best_rot, best_n = 0, -1
        for rot in (0, 90, 180, 270):
            trial = png
            if rot:
                trial = png.with_name(f"{png.stem}_f{rot}.png")
                shutil.copy(png, trial)
                subprocess.run(["sips", "-r", str(rot), str(trial)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                ink, _ = omr.load_binary(str(trial))
                n = len(extract.merge_split_systems(omr.build_systems(ink)))
            except Exception:
                n = -1
            if n > best_n:
                best_rot, best_n = rot, n

    # now rotate the full-resolution image once, the way the trials chose
    full = png
    if best_rot:
        full = png.with_name(f"{png.stem}_r{best_rot}.png")
        shutil.copy(png, full)
        subprocess.run(["sips", "-r", str(best_rot), str(full)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return full, best_n, best_rot


# Pages of one piece are held until the batch is closed. The key signature is
# a property of the piece, not of a page -- pooling the readings from every
# page is what makes it reliable -- and bar numbers have to run straight
# through, so a page cannot be finished on its own.
BATCH = []


def add_page(png: Path, name: str, rotation: int):
    first = (BATCH[-1]["last_bar"] + 1) if BATCH else 1
    bars, problems, votes = T.transcribe(str(png), f"u{len(BATCH)}", None, first)
    BATCH.append({"name": name, "bars": bars, "votes": votes,
                  "problems": problems, "rotation": rotation,
                  "last_bar": bars[-1]["bar"] if bars else first - 1})
    return {"page": len(BATCH), "name": name, "rotation": rotation,
            "systems": len({b["sys"] for b in bars}), "bars": len(bars)}


def finish_batch():
    if not BATCH:
        raise ValueError("no pages were added")
    all_bars = [b for p in BATCH for b in p["bars"]]
    keysig, ksdesc = T.resolve_keysig([v for p in BATCH for v in p["votes"]])
    T.apply_keysig(all_bars, keysig)
    rows = T.by_system(all_bars)
    ties = sum(1 for b in all_bars for n in b["notes"] if n.get("tied"))
    notes = sum(len(b["notes"]) for b in all_bars)
    return {"systems": rows, "key": ksdesc, "ties": ties, "notes": notes,
            "bars": len(all_bars), "pages": [p["name"] for p in BATCH],
            "problems": [x for p in BATCH for x in p["problems"]]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(paths.WEB), **kw)

    def guess_type(self, path):
        """Declare UTF-8. Without it the browser guesses, and the page's
        typographic characters arrive as mojibake."""
        t = super().guess_type(path)
        if t in ("text/html", "text/css", "application/javascript", "text/plain"):
            return t + "; charset=utf-8"
        return t

    def log_message(self, fmt, *args):
        sys.stderr.write("  " + fmt % args + "\n")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.path = "/studio.html"
        return super().do_GET()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/reset":
            BATCH.clear()
            return self._json(200, {"ok": True})
        if self.path == "/api/save":
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                rows = data.get("systems", [])
                lines = ["Corrected tablature", "5=blow 5  |  -5=draw 5  |  *=slide in", ""]
                for r in rows:
                    flag = " *" if r.get("edited") else "  "
                    lines.append(f"bars {r.get('bars',''):>9}{flag}  {r.get('tab','')}")
                (paths.OUT / "tab-corrected.txt").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8")
                json.dump(rows, open(paths.OUT / "corrections.json", "w"), indent=1)
                edited = sum(1 for r in rows if r.get("edited"))
                sys.stderr.write(f"  saved {len(rows)} lines, {edited} edited\n")
                return self._json(200, {"ok": True, "lines": len(rows), "edited": edited})
            except Exception as exc:
                traceback.print_exc()
                return self._json(400, {"error": str(exc)})
        if self.path == "/api/finish":
            try:
                return self._json(200, finish_batch())
            except Exception as exc:
                traceback.print_exc()
                return self._json(400, {"error": str(exc)})
        if self.path != "/api/page":
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            name = self.headers.get("X-Filename", "upload.png")
            suffix = Path(name).suffix or ".png"
            with tempfile.TemporaryDirectory() as td:
                raw = Path(td) / f"page{suffix}"
                raw.write_bytes(self.rfile.read(n))
                png = to_png(raw)
                best, count, rot = orient(png)
                if count < 1:
                    raise ValueError("no staves found in this image")
                sys.stderr.write(f"  {name}: {count} systems, rotated {rot} deg\n")
                result = add_page(best, name, rot)
            self._json(200, result)
        except Exception as exc:
            traceback.print_exc()
            self._json(400, {"error": str(exc)})


if __name__ == "__main__":
    print(f"transcriber studio -> http://localhost:{PORT}")
    print("photos are processed on this machine; nothing is uploaded anywhere")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

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


def transcribe_one(png: Path, tag="upload"):
    bars, problems, votes = T.transcribe(str(png), tag, None, 1)
    keysig, ksdesc = T.resolve_keysig(votes)
    T.apply_keysig(bars, keysig)
    rows = T.by_system(bars)
    ties = sum(1 for b in bars for n in b["notes"] if n.get("tied"))
    notes = sum(len(b["notes"]) for b in bars)
    return {"systems": rows, "key": ksdesc, "ties": ties, "notes": notes,
            "bars": len(bars), "problems": problems}


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

    def do_POST(self):
        if self.path != "/api/transcribe":
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
                result = transcribe_one(best)
                result["rotation"] = rot
                result["file"] = name
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            traceback.print_exc()
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    print(f"transcriber studio -> http://localhost:{PORT}")
    print("photos are processed on this machine; nothing is uploaded anywhere")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

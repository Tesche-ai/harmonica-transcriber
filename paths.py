"""Project paths, resolved from this file so scripts run from any directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCANS = ROOT / "scans"
OUT = ROOT / "out"
OVERLAYS = OUT / "overlays"
WEB = ROOT / "web"

# Deskewed page scans. Add pages here as the library grows.
PAGES = {
    "p1": SCANS / "page1.png",
    "p2": SCANS / "page2.png",
}


def page(key):
    """Accept either a page key ('p1') or a path to an image."""
    if key in PAGES:
        return PAGES[key]
    p = Path(key)
    if p.exists():
        return p
    raise SystemExit(f"unknown page {key!r}; known keys: {', '.join(PAGES)}")


def ensure_dirs():
    OUT.mkdir(exist_ok=True)
    OVERLAYS.mkdir(parents=True, exist_ok=True)

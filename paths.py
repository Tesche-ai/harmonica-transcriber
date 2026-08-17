"""Project paths, resolved from this file so scripts run from any directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCANS = ROOT / "scans"
OUT = ROOT / "out"
OVERLAYS = OUT / "overlays"
WEB = ROOT / "web"

def _discover():
    """Every deskewed page in scans/, in filename order.

    Reachable as p1, p2, ... and by filename stem, so adding a piece is a
    matter of dropping images in rather than editing code.
    """
    pages = {}
    for i, p in enumerate(sorted(SCANS.glob("*.png")), start=1):
        pages.setdefault(f"p{i}", p)
        pages.setdefault(p.stem, p)
    return pages


PAGES = _discover()


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

"""Turn phone photos into the deskewed page scans the pipeline reads.

macOS only -- it shells out to `sips`, which handles HEIC natively.

The photos of this chart were taken with the page sideways, so each needs a
90 degree rotation. Set the rotation per source file below. Everything after
this step assumes the staves run left to right; mild perspective and page curl
are fine, because omr.fit_system models them.
"""
import subprocess
import sys

import paths

# source file in scans/original -> (output name, clockwise rotation in degrees)
SOURCES = {
    "IMG_0385.HEIC": ("page1.png", 90),
    "IMG_0386.HEIC": ("page2.png", 90),
    # IMG_0384.HEIC is an earlier, more angled shot of page 1 -- kept for
    # reference but not used; page1.png comes from the flatter IMG_0385.
}


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    src_dir = paths.SCANS / "original"
    for name, (out_name, rot) in SOURCES.items():
        src = src_dir / name
        if not src.exists():
            print(f"skip {name}: not found in {src_dir}")
            continue
        dst = paths.SCANS / out_name
        tmp = paths.SCANS / f".{out_name}.tmp.png"
        run(["sips", "-s", "format", "png", str(src), "--out", str(tmp)])
        if rot:
            run(["sips", "-r", str(rot), str(tmp)])
        tmp.replace(dst)
        print(f"{name} -> {dst.name} (rotated {rot} deg)")


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("prepare.py uses macOS `sips`; convert HEIC another way elsewhere")
    main()

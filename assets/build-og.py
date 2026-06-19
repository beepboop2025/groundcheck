#!/usr/bin/env python3
"""Rasterize assets/og.html to social-card PNGs via headless Chrome + Pillow.

Renders at 2x for crisp text, then downsamples (LANCZOS) to exact dimensions.
Outputs:
  assets/og-card.png        1200x630  (OpenGraph / DEV cover / Twitter)
  assets/github-social.png  1280x640  (GitHub repo social preview)
"""
import subprocess, sys, tempfile, os
from pathlib import Path
from PIL import Image

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = Path(__file__).resolve().parent
SRC = HERE / "og.html"
SCALE = 2

TARGETS = [
    ("og-card.png", 1200, 630),
    ("github-social.png", 1280, 640),
]


def render(html_path: Path, w: int, h: int, out: Path):
    with tempfile.TemporaryDirectory() as prof:
        big = out.with_suffix(".2x.png")
        cmd = [
            CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", f"--force-device-scale-factor={SCALE}",
            f"--window-size={w},{h}", f"--user-data-dir={prof}",
            f"--screenshot={big}", html_path.as_uri(),
        ]
        # headless=new sometimes won't exit cleanly; the screenshot lands on disk
        # before it would anyway, so cap the wait and treat file-present as success.
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        if not big.exists():
            raise RuntimeError(f"render produced no screenshot at {big}")
        img = Image.open(big).convert("RGB")
        img = img.resize((w, h), Image.LANCZOS)
        img.save(out, "PNG", optimize=True)
        big.unlink(missing_ok=True)
        kb = out.stat().st_size // 1024
        print(f"  ✓ {out.name}  {w}x{h}  ({kb} KB)")


def main():
    if not Path(CHROME).exists():
        sys.exit("Chrome not found at expected path")
    base = SRC.read_text()
    for name, w, h in TARGETS:
        out = HERE / name
        # surgically swap only the :root size vars for the GitHub aspect ratio
        html = base.replace("--w: 1200px", f"--w: {w}px").replace("--h: 630px", f"--h: {h}px")
        tmp = HERE / f".tmp-{name}.html"
        tmp.write_text(html)
        try:
            render(tmp, w, h, out)
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

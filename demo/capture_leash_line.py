"""Render the real post-poisoned-run Leash Line from demo/run_demo.sh to demo/leash-line.png.

Runs the deterministic demo (real Hermes, throwaway HOME), keeps the ANSI truecolor output of the first
`hermes mandate status nightly-triage` after the poisoned run, turns it into HTML spans, and has headless
Chrome screenshot it. Usage: python3 demo/capture_leash_line.py [out.png]
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BG, FG = "#0B1426", "#E8EEF8"
SGR = re.compile(r"\x1b\[([0-9;]*)m")


def ansi_to_html(text: str) -> str:
    out, color, bold, pos = [], None, False, 0

    def emit(chunk: str) -> None:
        if not chunk:
            return
        style = (f"color:{color};" if color else "") + ("font-weight:700;" if bold else "")
        out.append(f'<span style="{style}">{html.escape(chunk)}</span>' if style else html.escape(chunk))

    for m in SGR.finditer(text):
        emit(text[pos:m.start()])
        pos = m.end()
        codes = [int(c) for c in m.group(1).split(";") if c] or [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                color, bold = None, False
            elif c == 1:
                bold = True
            elif c == 22:
                bold = False
            elif c == 39:
                color = None
            elif c == 38 and codes[i + 1:i + 2] == [2]:
                r, g, b = codes[i + 2:i + 5]
                color = f"#{r:02X}{g:02X}{b:02X}"
                i += 4
            i += 1
    emit(text[pos:])
    return "".join(out)


def main() -> int:
    out_png = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "demo" / "leash-line.png"
    env = {**os.environ, "DEMO_PAUSE": "0", "MANDATE_FORCE_COLOR": "1"}
    env.pop("NO_COLOR", None)
    demo = subprocess.run([str(ROOT / "demo" / "run_demo.sh")], env=env, capture_output=True, text=True, check=True)
    lines = demo.stdout.splitlines()
    plain = [SGR.sub("", ln) for ln in lines]
    start = next(i for i, ln in enumerate(plain) if ln.startswith("$ hermes mandate status nightly-triage"))
    end = next(i for i in range(start + 1, len(plain)) if plain[i].startswith("$ hermes mandate "))
    block = "\n".join(lines[start:end]).rstrip()
    rows = len(lines[start:end])
    cols = max(len(p) for p in plain[start:end])
    page = f"""<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;background:{BG}}}
pre{{margin:0;padding:28px 32px;color:{FG};font:18px/1.45 'IBM Plex Mono','DejaVu Sans Mono',monospace;white-space:pre}}
</style><pre>{ansi_to_html(block)}</pre>"""
    with tempfile.TemporaryDirectory() as tmp:
        page_path = Path(tmp) / "leash.html"
        page_path.write_text(page, encoding="utf-8")
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or "google-chrome"
        width, height = int(cols * 10.9) + 64, int(rows * 26.1) + 56
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                        f"--user-data-dir={tmp}/profile", f"--window-size={width},{height}",
                        f"--screenshot={out_png}", page_path.as_uri()],
                       check=True, capture_output=True)
    print(out_png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Leash Line: one track per rule family. Allowed band, leash post, blocked notches."""

from __future__ import annotations

import os
import sys

from .policy import Policy

ALLOW = (0x12, 0xE0, 0x8A)
DENY = (0xFF, 0x4D, 0x2E)
PENDING = (0xF5, 0xC5, 0x42)
TEXT = (0xE8, 0xEE, 0xF8)
DIM = (0x6B, 0x7A, 0x99)
SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
FAMILY_OF = {"tools": "tools", "paths.read": "paths", "paths.write": "paths", "commands": "commands",
             "hosts": "hosts", "limits": "paths"}
TRACKS = ("tools", "paths", "commands", "hosts")


def use_color(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("MANDATE_FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


class Paint:
    def __init__(self, on: bool):
        self.on = on

    def __call__(self, text: str, rgb: tuple[int, int, int], bold: bool = False) -> str:
        if not self.on or not text:
            return text
        b = "\x1b[1m" if bold else ""
        return f"{b}\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\x1b[0m"


def _rules(pol: Policy | None, track: str):
    if pol is None:
        return []
    if track == "paths":
        return pol.reads + pol.writes
    return {"tools": pol.tools, "commands": pol.commands, "hosts": pol.hosts}[track]


def leash_line(job: str, active: Policy | None, version: int | None, sha: str | None, mode: str,
               pending: Policy | None, drift: list, runs: int, color: bool | None = None,
               history_runs: int = 0) -> str:
    paint = Paint(use_color() if color is None else color)
    state = "ENFORCING" if mode == "enforce" and active is not None else "OBSERVING"
    state_rgb = DENY if state == "ENFORCING" else PENDING
    head = [paint("MANDATE", TEXT, True) + paint(" \u25b8 ", DIM) + paint(job, TEXT, True)]
    if version is not None:
        head.append(paint(f"v{version} sha256:{(sha or '')[:12]}", DIM))
    head.append(paint(state, state_rgb, True))
    lines = ["  ".join(head)]
    notches: dict[str, list] = {t: [] for t in TRACKS}
    for d in sorted(drift, key=lambda r: r["id"]):
        notches[FAMILY_OF.get(d["family"], "commands")].append(d)
    bands = {}
    for t in TRACKS:
        rules = _rules(active, t)
        peak = max([r.evidence for r in rules] or [1]) or 1
        cells = [SPARK[min(7, max(0, round(7 * r.evidence / peak)))] * 2 for r in rules]
        active_vals = {r.value for r in rules}
        new = [r for r in _rules(pending, t) if r.value not in active_vals]
        bands[t] = (cells, new)
    width = max([len(c) * 2 + len(n) * 2 for c, n in bands.values()] + [8])
    for t in TRACKS:
        cells, new = bands[t]
        used = len(cells) * 2 + len(new) * 2
        band = paint("".join(cells), ALLOW) + paint("\u25c6\u25c6" * len(new), PENDING)
        filler = paint("\u2500" * (width - used), DIM)
        marks = " ".join((paint("\u25bc", DENY, True) + paint(f"#{d['id']}", DENY)) if d["status"] == "open"
                         else (paint("\u25b3", PENDING, True) + paint(f"#{d['id']}", PENDING))
                         for d in notches[t][-6:])
        if len(notches[t]) > 6:
            marks += paint(f" +{len(notches[t]) - 6}", DENY)
        meta = paint(f"{len(cells)} allowed", ALLOW)
        if new:
            meta += paint(f" \u00b7 {len(new)} pending", PENDING)
        if notches[t]:
            meta += paint(f" \u00b7 {len(notches[t])} blocked", DENY)
        lines.append(f"  {paint(t.ljust(9), TEXT)}\u2595{band}{filler}{paint(chr(0x2503), TEXT, True)} "
                     f"{marks}{'  ' if marks else ''}{meta}")
    legend = []
    for t in TRACKS:
        top = sorted(_rules(active, t), key=lambda r: (-r.evidence, r.value))[:3]
        if top:
            legend.append(f"{t}: " + ", ".join(f"{r.value} {r.evidence}x" for r in top))
    for item in legend:
        lines.append("  " + paint(item[:110], DIM))
    tail = f"{runs} run(s) observed"
    if history_runs:
        tail += f" ({history_runs} learned from Hermes history)"
    if drift:
        last = drift[0] if drift[0]["id"] >= drift[-1]["id"] else drift[-1]
        tail += f" \u00b7 last block #{last['id']}: {last['call'][:70]}"
    lines.append("  " + paint(tail, DIM))
    return "\n".join(lines)


def stamp(job: str, version: int, sha: str, color: bool | None = None) -> str:
    paint = Paint(use_color() if color is None else color)
    inner = [f"  APPROVED  \u00b7  MANDATE v{version}  ", f"  {job[:24]:<24}", f"  sha256:{sha[:16]}  "]
    w = max(len(s) for s in inner)
    top = "\u2554" + "\u2550" * w + "\u2557"
    bot = "\u255a" + "\u2550" * w + "\u255d"
    body = ["\u2551" + s.ljust(w) + "\u2551" for s in inner]
    return "\n".join(paint(x, ALLOW, True) for x in [top, *body, bot])

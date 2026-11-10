"""Path normalization against a workdir, and glob matching for policy rules."""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class PathRef:
    value: str      # workdir-relative ("src/a.py", ".") when inside, else absolute realpath
    inside: bool
    valid: bool = True

    def __str__(self) -> str:
        return self.value


def resolve(path: str, cwd: str, root: str) -> PathRef:
    """Resolve *path* (relative to *cwd*) with symlinks, then express it relative to *root*."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return PathRef(f"<invalid:{str(path)[:60]!r}>", False, False)
    p = os.path.expanduser(path) if path.startswith("~") else path
    if p.startswith("~"):
        return PathRef(f"<invalid:{path[:60]!r}>", False, False)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    real = os.path.realpath(p)
    root_real = os.path.realpath(root)
    try:
        inside = os.path.commonpath([real, root_real]) == root_real
    except ValueError:
        inside = False
    if inside:
        rel = os.path.relpath(real, root_real).replace(os.sep, "/")
        return PathRef(rel, True)
    return PathRef(real.replace(os.sep, "/"), False)


@lru_cache(maxsize=1024)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def glob_match(pattern: str, ref: PathRef) -> bool:
    """Relative patterns only match paths inside the workdir; absolute patterns only outside."""
    if not ref.valid:
        return False
    if pattern.startswith("/") != (not ref.inside):
        return False
    if pattern == "**" and ref.inside:
        return True
    return bool(_glob_regex(pattern).match(ref.value))


def generalize_read(rel: str) -> str:
    parts = rel.split("/")
    if rel == "." or len(parts) == 1:
        return rel
    return parts[0] + "/**"


def generalize_write(rel: str) -> str:
    d, name = posixpath.split(rel)
    if not d:
        return rel
    ext = posixpath.splitext(name)[1]
    return f"{d}/*{ext}"

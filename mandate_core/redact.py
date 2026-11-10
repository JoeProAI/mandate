"""Redaction and truncation for anything Mandate stores or prints."""

from __future__ import annotations

import json
import re
from typing import Any

MAX_VALUE = 240
CONTENT_KEYS = {"content", "old_string", "new_string", "code", "patch", "text", "body", "data", "file_text",
                "input", "stdin"}
_SECRET_PATTERNS = [
    re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"(?i)\b(bearer|token|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)((?:pass(?:word)?|secret|token|api[_-]?key|auth)[\"']?\s*[=:]\s*)[^\s&\"']+"),
    re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
]


def redact(text: str, limit: int = MAX_VALUE) -> str:
    s = str(text)
    for pat in _SECRET_PATTERNS:
        if pat.groups and pat.pattern.startswith("(?i)(("):
            s = pat.sub(lambda m: m.group(1) + "[REDACTED]", s)
        elif pat.pattern.startswith("(?i)(://"):
            s = pat.sub(lambda m: m.group(1) + "[REDACTED]@", s)
        else:
            s = pat.sub("[REDACTED]", s)
    if len(s) > limit:
        s = s[: limit - 1] + "\u2026"
    return s


def redact_args(args: Any) -> Any:
    """Copy of tool args safe to store: content-bearing values replaced by their length."""
    if isinstance(args, dict):
        out = {}
        for k in sorted(args, key=str):
            v = args[k]
            if str(k).lower() in CONTENT_KEYS and isinstance(v, str):
                out[str(k)] = f"<{len(v)} chars>"
            else:
                out[str(k)] = redact_args(v)
        return out
    if isinstance(args, list):
        return [redact_args(v) for v in args[:20]]
    if isinstance(args, str):
        return redact(args)
    if isinstance(args, (int, float, bool)) or args is None:
        return args
    return redact(repr(args))


def call_summary(tool: str, args: Any, limit: int = 300) -> str:
    safe = redact_args(args if isinstance(args, dict) else {"args": args})
    if tool == "terminal" and isinstance(safe, dict) and isinstance(safe.get("command"), str):
        text = f"terminal: {safe['command']}"
    else:
        text = f"{tool} {json.dumps(safe, sort_keys=True, ensure_ascii=False)}"
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"

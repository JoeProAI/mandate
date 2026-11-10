"""Turn one tool call into a CallShape: the facts a policy can be checked against."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from . import shell
from .hosts import host_of
from .paths import PathRef, resolve
from .redact import call_summary, redact

EXEMPT_TOOLS = {"mandate_status"}
WRITE_TOOLS = {"write_file", "patch"}
READ_PATH_TOOLS = {"read_file": "path", "search_files": "path"}
URL_KEYS = {"url", "urls", "uri", "endpoint", "base_url", "href", "link"}
PATH_KEYS = {"path", "file", "file_path", "filepath", "filename", "dir", "directory", "workdir", "cwd",
             "target_path", "source_path", "dest", "destination"}
CODE_TOOLS = {"execute_code": "code"}
_PATCH_FILE_PREFIXES = ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")


@dataclass
class CallShape:
    tool: str
    summary: str
    reads: list[PathRef] = field(default_factory=list)
    writes: list[PathRef] = field(default_factory=list)
    segments: list[shell.Segment] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    write_count: int = 0
    exempt: bool = False

    def to_json(self) -> dict:
        return {
            "tool": self.tool,
            "summary": self.summary,
            "reads": [[r.value, r.inside, r.valid] for r in self.reads],
            "writes": [[r.value, r.inside, r.valid] for r in self.writes],
            "segments": [{"raw": redact(s.raw, 300), "key": s.key(), "words": list(s.words), "flags": list(s.flags),
                          "env": list(s.env), "opaque": s.opaque} for s in self.segments],
            "hosts": [redact(h, 200) for h in self.hosts],
            "write_count": self.write_count,
        }

    @classmethod
    def from_json(cls, data: dict) -> "CallShape":
        segs = []
        for s in data.get("segments", []):
            seg = shell.Segment(raw=s["raw"], head=(s["words"] or [""])[0], words=tuple(s["words"]),
                                flags=tuple(s["flags"]), env=tuple(s["env"]), opaque=s["opaque"])
            segs.append(seg)
        return cls(
            tool=data["tool"], summary=data.get("summary", ""),
            reads=[PathRef(v, i, ok) for v, i, ok in data.get("reads", [])],
            writes=[PathRef(v, i, ok) for v, i, ok in data.get("writes", [])],
            segments=segs, hosts=list(data.get("hosts", [])), write_count=int(data.get("write_count", 0)),
        )


def _strs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


def _terminal(shape: CallShape, args: dict, cwd: str, root: str) -> None:
    command = args.get("command", "")
    wd = args.get("workdir")
    if isinstance(wd, str) and wd:
        ref = resolve(wd, cwd, root)
        shape.reads.append(ref)
        cwd = os.path.join(root, ref.value) if ref.inside else ref.value
    segments = shell.parse(command)
    if not segments and isinstance(command, str) and command.strip():
        segments = [shell.Segment(raw=command, opaque="no command found")]
    for seg in segments:
        shape.segments.append(seg)
        if seg.opaque:
            continue
        for op, p in shell.path_candidates(seg):
            ref = resolve(p, cwd, root)
            (shape.writes if op == "write" else shape.reads).append(ref)
        for url in shell.host_candidates(seg):
            shape.hosts.append(host_of(url))
        if seg.cd_target is not None:
            ref = resolve(seg.cd_target, cwd, root)
            cwd = os.path.join(root, ref.value) if ref.inside else ref.value
        if seg.is_write:
            shape.write_count += 1


def normalize_call(tool: str, args: Any, cwd: str, root: str | None = None) -> CallShape:
    """*cwd* is where relative paths resolve; *root* is the job workdir that "inside" refers to."""
    root = root or cwd
    tool = str(tool or "")
    shape = CallShape(tool=tool, summary=call_summary(tool, args))
    if tool in EXEMPT_TOOLS:
        shape.exempt = True
        return shape
    if not isinstance(args, dict):
        shape.segments.append(shell.Segment(raw=f"{tool}:<non-object args>", opaque="arguments are not an object"))
        return shape
    if tool == "terminal":
        _terminal(shape, args, cwd, root)
    elif tool in CODE_TOOLS:
        code = args.get(CODE_TOOLS[tool], "")
        digest = hashlib.sha256(str(code).encode("utf-8")).hexdigest()[:16]
        shape.segments.append(shell.Segment(raw=f"{tool}:sha256:{digest}", opaque="inline code"))
    elif tool in WRITE_TOOLS:
        paths = _strs(args.get("path"))
        if tool == "patch" and isinstance(args.get("patch"), str):
            for line in args["patch"].splitlines():
                for prefix in _PATCH_FILE_PREFIXES:
                    if line.startswith(prefix):
                        paths.append(line[len(prefix):].strip())
        for p in paths:
            shape.writes.append(resolve(p, cwd, root))
        shape.write_count = 1
    elif tool in READ_PATH_TOOLS:
        p = args.get(READ_PATH_TOOLS[tool]) or "."
        shape.reads.append(resolve(str(p), cwd, root))
    # Generic extraction for every tool, including unknown MCP/plugin tools.
    for key in sorted(args, key=str):
        k = str(key).lower()
        if tool == "terminal" and k in ("command", "workdir"):
            continue
        if tool in READ_PATH_TOOLS and k == READ_PATH_TOOLS[tool]:
            continue
        if tool in WRITE_TOOLS and k == "path":
            continue
        if k in URL_KEYS:
            for url in _strs(args[key]):
                shape.hosts.append(host_of(url))
        elif k in PATH_KEYS:
            for p in _strs(args[key]):
                shape.reads.append(resolve(p, cwd, root))
    return shape


def shape_digest(shape: CallShape) -> str:
    return hashlib.sha256(json.dumps(shape.to_json(), sort_keys=True).encode()).hexdigest()[:16]

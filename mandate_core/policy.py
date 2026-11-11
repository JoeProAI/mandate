"""Mandate policy: compile from observations, render/parse deterministic YAML, check calls."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .hosts import host_match
from .normalize import CallShape
from .paths import PathRef, generalize_read, generalize_write, glob_match

FORMAT_VERSION = 1
MIN_RUNS = 3
_ENV_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=$")
_SEEN = re.compile(r"seen (\d+)x")


@dataclass
class Rule:
    value: str
    evidence: int = 0


@dataclass
class Policy:
    job: str
    workdir: str = ""
    observed_runs: int = 0
    tools: list[Rule] = field(default_factory=list)
    reads: list[Rule] = field(default_factory=list)
    writes: list[Rule] = field(default_factory=list)
    commands: list[Rule] = field(default_factory=list)
    hosts: list[Rule] = field(default_factory=list)
    max_writes_per_run: int | None = None

    def families(self) -> dict[str, list[Rule]]:
        return {"tools": self.tools, "paths.read": self.reads, "paths.write": self.writes,
                "commands": self.commands, "hosts": self.hosts}

    def rule_count(self) -> int:
        return sum(len(v) for v in self.families().values()) + (self.max_writes_per_run is not None)


@dataclass
class Violation:
    family: str        # tools | paths.read | paths.write | commands | hosts | limits
    subject: str       # the exact thing that did not match
    reason: str

    def suggestion(self) -> str:
        if self.family == "commands" and self.subject.startswith("="):
            return f"commands: {json.dumps(self.subject)}"
        return f"{self.family}: {json.dumps(self.subject)}"


# ---------------------------------------------------------------- command rules

@dataclass(frozen=True)
class CommandRule:
    exact: str | None
    words: tuple[str, ...]
    flags: frozenset[str]
    env: frozenset[str]
    any_flags: bool

    @classmethod
    def parse(cls, text: str) -> "CommandRule":
        if text.startswith("="):
            return cls(text[1:], (), frozenset(), frozenset(), False)
        try:
            toks = shlex.split(text)
        except ValueError:
            return cls(text, (), frozenset(), frozenset(), False)
        env = frozenset(t[:-1] for t in toks if _ENV_TOKEN.match(t))
        flags = [t for t in toks if t.startswith("-") and not _ENV_TOKEN.match(t)]
        words = tuple(t for t in toks if not t.startswith("-") and not _ENV_TOKEN.match(t))
        return cls(None, words, frozenset(f for f in flags if f != "-*"), env, "-*" in flags)

    def matches(self, seg) -> bool:
        if self.exact is not None:
            return _ws(seg.raw) == _ws(self.exact)
        if seg.opaque:
            return False
        if not self.words:
            return False
        if self.words[-1] == "*":
            prefix = self.words[:-1]
            if tuple(seg.words[: len(prefix)]) != prefix or len(seg.words) < len(prefix):
                return False
        elif tuple(seg.words) != self.words:
            return False
        if not set(seg.env) <= self.env:
            return False
        return self.any_flags or set(seg.flags) <= self.flags


def _ws(s: str) -> str:
    return " ".join(s.split())


# ---------------------------------------------------------------- checking

def _path_ok(ref: PathRef, rules: Iterable[Rule]) -> bool:
    return any(glob_match(r.value, ref) for r in rules)


def _read_ok(ref: PathRef, policy: "Policy") -> bool:
    """A read rule `d/**` also covers reading the directory `d` itself (a search or listing of it)."""
    if _path_ok(ref, policy.reads) or _path_ok(ref, policy.writes):
        return True
    return ref.valid and ref.inside and any(r.value == ref.value.rstrip("/") + "/**" for r in policy.reads)


# Tool families: the model swaps these freely between runs. A tool counts as approved when another tool of its
# family is approved; the path rules still decide what it may touch.
READ_FAMILY = frozenset({"read_file", "search_files"})
EDIT_FAMILY = frozenset({"write_file", "patch"})

# Read-only shell commands that may be widened (new flags, read-back of approved paths). Value: flags that make
# the command write, run code or read outside its operands, so they are never widened.
READ_ONLY_HEADS: dict[str, frozenset[str]] = {
    "cat": frozenset(), "head": frozenset(), "tail": frozenset(), "wc": frozenset(), "nl": frozenset(),
    "tac": frozenset(), "ls": frozenset(), "stat": frozenset(), "file": frozenset(), "du": frozenset(),
    "diff": frozenset(), "cut": frozenset(), "tr": frozenset(),
    "grep": frozenset({"-r", "-R", "--recursive", "--dereference-recursive", "-d", "--directories"}),
    "egrep": frozenset({"-r", "-R", "--recursive", "--dereference-recursive", "-d", "--directories"}),
    "fgrep": frozenset({"-r", "-R", "--recursive", "--dereference-recursive", "-d", "--directories"}),
    "sort": frozenset({"-o", "--output", "--compress-program", "-T", "--temporary-directory"}),
    "uniq": frozenset(),
}
# Commands whose only effect is printing their own arguments.
PRINT_HEADS = frozenset({"echo", "printf", "true", "pwd", "date"})
MKDIR_FLAGS = frozenset({"-p", "--parents", "-v", "--verbose"})
REWRITE_EXTRA = 4   # rewrites of an already-written path allowed per run on top of max_writes_per_run


def _plain(seg) -> bool:
    """A parsed segment with nothing hidden: no opaque part, env prefix, env wrapper or pathed head."""
    import posixpath
    return (not seg.opaque and not seg.env and not seg.extra.get("env_wrapper")
            and not posixpath.dirname(seg.head or "") and "refs" in seg.extra)


def _seg_hosts(seg) -> list[str]:
    from .shell import host_candidates
    return host_candidates(seg)


def _readonly_ok(seg, policy: Policy, rules: list["CommandRule"]) -> bool:
    """A read-only command whose every operand is a path the mandate already lets the job read or write.

    Covers flag variance of a learned read-only command (grep -c vs grep -ic), and read-back of approved paths
    (cat reports/x.md). Never covers a write, a redirect out, a network destination, or a path outside the
    approved rules, so it does not widen what the job can reach, only how it phrases reading it."""
    if not _plain(seg) or seg.is_write or _seg_hosts(seg):
        return False
    import posixpath
    head = posixpath.basename(seg.head).lower()
    if any(op.lstrip("0123456789") not in ("<",) for op, _ in seg.redirects if _ not in
           ("/dev/null", "/dev/stdout", "/dev/stderr")):
        return False
    refs = [r for _, r in seg.extra["refs"]]
    if head in PRINT_HEADS:
        return not refs and not seg.redirects
    banned = READ_ONLY_HEADS.get(head)
    if banned is None or set(seg.flags) & banned:
        return False
    if head == "uniq" and len(seg.positionals) > 1:   # uniq IN OUT writes OUT
        return False
    if not all(r.valid and r.inside and (_path_ok(r, policy.reads) or _path_ok(r, policy.writes)) for r in refs):
        return False
    if refs:
        return True
    # No operands: reads stdin (or lists the workdir for ls/du). Only widen a command the job already uses.
    return any(r.exact is None and r.words and posixpath.basename(r.words[0]).lower() == head for r in rules)


def _write_dirs(policy: Policy) -> set[str]:
    """Every ancestor directory of a literal directory part of an inside write rule."""
    import posixpath
    out: set[str] = set()
    for r in policy.writes:
        d = posixpath.dirname(r.value)
        if not d or r.value.startswith("/") or any(c in d for c in "*?[]"):
            continue
        while d and d not in (".", "/"):
            out.add(d)
            d = posixpath.dirname(d)
    return out


def _mkdir_ok(seg, dirs: set[str]) -> bool:
    import posixpath
    if not _plain(seg) or seg.redirects or posixpath.basename(seg.head).lower() != "mkdir":
        return False
    if not set(seg.flags) <= MKDIR_FLAGS:
        return False
    refs = [r for _, r in seg.extra["refs"]]
    return bool(refs) and all(r.valid and r.inside and r.value in dirs for r in refs)


def _mkdir_refs(shape: CallShape, dirs: set[str]) -> set[PathRef]:
    return {r for s in shape.segments if s.is_write and _mkdir_ok(s, dirs) for _, r in s.extra["refs"]}


def write_cost(shape: CallShape, policy: Policy, written: set[str] | None = None) -> int:
    """Writes this call charges against max_writes_per_run: mkdir of an approved parent dir is free, and so is
    rewriting a path this run already wrote (capped separately in check)."""
    dirs = _write_dirs(policy)
    cost = shape.write_count - sum(1 for s in shape.segments if s.is_write and _mkdir_ok(s, dirs))
    mk = _mkdir_refs(shape, dirs)
    refs = [r for r in shape.writes if r not in mk]
    if cost > 0 and written is not None and refs and len(refs) >= cost \
            and all(r.valid and r.inside and r.value in written for r in refs):
        return 0
    return max(cost, 0)


def check(shape: CallShape, policy: Policy, writes_so_far: int = 0, written: set[str] | None = None,
          raw_writes_so_far: int | None = None) -> list[Violation]:
    """Every way *shape* falls outside *policy*. Empty list means allowed.

    writes_so_far counts distinct writes this run (what write_cost charged); *written* is the set of inside
    paths this run already wrote; raw_writes_so_far counts every write call, rewrites included."""
    if shape.exempt:
        return []
    out: list[Violation] = []
    rules = [CommandRule.parse(r.value) for r in policy.commands]
    tools = {r.value for r in policy.tools}
    dirs = _write_dirs(policy)
    seg_ok = {}
    for i, seg in enumerate(shape.segments):
        if any(r.matches(seg) for r in rules):
            seg_ok[i] = "rule"
        elif seg.is_write and _mkdir_ok(seg, dirs):
            seg_ok[i] = "mkdir"
        elif _readonly_ok(seg, policy, rules):
            seg_ok[i] = "readonly"
    if shape.tool not in tools:
        fam_ok = ((shape.tool in READ_FAMILY and tools & READ_FAMILY)
                  or (shape.tool in EDIT_FAMILY and tools & EDIT_FAMILY))
        # A terminal call made only of read-back / mkdir-of-parent segments needs no terminal rule.
        term_ok = (shape.tool == "terminal" and shape.segments and not shape.hosts
                   and all(seg_ok.get(i) in ("mkdir", "readonly") for i in range(len(shape.segments))))
        if not (fam_ok or term_ok):
            out.append(Violation("tools", shape.tool, f"tool '{shape.tool}' is not in the mandate"))
    for ref in shape.reads:
        if not _read_ok(ref, policy):
            # A file-name listing (search_files target=files) inside the workdir reveals names, not contents.
            if shape.listing and ref.valid and ref.inside and policy.reads:
                continue
            where = "inside" if ref.inside else "outside the workdir"
            out.append(Violation("paths.read", ref.value, f"read of {ref.value} ({where}) matches no path rule"))
    mk = {r for i, s in enumerate(shape.segments) if seg_ok.get(i) == "mkdir" for _, r in s.extra["refs"]}
    for ref in shape.writes:
        if ref in mk:
            continue
        if not _path_ok(ref, policy.writes):
            where = "inside" if ref.inside else "outside the workdir"
            out.append(Violation("paths.write", ref.value, f"write to {ref.value} ({where}) matches no write rule"))
    for i, seg in enumerate(shape.segments):
        if i in seg_ok:
            continue
        if seg.opaque:
            out.append(Violation("commands", "=" + _ws(seg.raw), f"opaque segment ({seg.opaque}): {seg.raw[:120]}"))
        else:
            out.append(Violation("commands", seg.key(), f"command '{seg.key()}' matches no command rule"))
    for h in shape.hosts:
        if not any(host_match(r.value, h) for r in policy.hosts):
            out.append(Violation("hosts", h, f"host {h} is not in the mandate"))
    if policy.max_writes_per_run is not None and shape.write_count:
        cost = write_cost(shape, policy, written)
        if cost and writes_so_far + cost > policy.max_writes_per_run:
            out.append(Violation("limits", f"max_writes_per_run={policy.max_writes_per_run}",
                                 f"write budget exceeded: {writes_so_far + cost} writes in this run, "
                                 f"mandate allows {policy.max_writes_per_run}"))
        elif not cost and raw_writes_so_far is not None and shape.writes \
                and raw_writes_so_far + 1 > policy.max_writes_per_run + REWRITE_EXTRA \
                and not all(r in mk for r in shape.writes):
            out.append(Violation("limits", f"max_writes_per_run={policy.max_writes_per_run}",
                                 f"rewrite cap exceeded: {raw_writes_so_far + 1} write calls in this run, "
                                 f"mandate allows {policy.max_writes_per_run} paths and {REWRITE_EXTRA} rewrites"))
    return out


# ---------------------------------------------------------------- compiling

def compile_policy(job: str, runs: dict[str, list[CallShape]], workdir: str = "") -> Policy:
    tools: Counter[str] = Counter()
    reads: Counter[str] = Counter()
    writes: Counter[str] = Counter()
    cmd_flags: dict[tuple, set[str]] = defaultdict(set)
    cmd_count: Counter[tuple] = Counter()
    exact: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    max_writes = 0
    for run_id in sorted(runs):
        run_writes = 0
        for shape in runs[run_id]:
            if shape.exempt:
                continue
            tools[shape.tool] += 1
            for ref in shape.reads:
                if ref.valid:
                    reads[generalize_read(ref.value) if ref.inside else ref.value] += 1
            for ref in shape.writes:
                if ref.valid:
                    writes[generalize_write(ref.value) if ref.inside else ref.value] += 1
            for seg in shape.segments:
                if seg.opaque:
                    exact["=" + _ws(seg.raw)] += 1
                    continue
                key = (tuple(seg.env), tuple(seg.words))
                cmd_flags[key].update(seg.flags)
                cmd_count[key] += 1
            for h in shape.hosts:
                if not h.startswith("<invalid"):
                    hosts[h] += 1
            run_writes += shape.write_count
        max_writes = max(max_writes, run_writes)
    commands: Counter[str] = Counter()
    for key, count in cmd_count.items():
        env, words = key
        toks = [f"{e}=" for e in env] + [_q(w) for w in words] + sorted(cmd_flags[key])
        commands[" ".join(toks)] += count
    commands.update(exact)
    # Drop read globs already covered by a write rule? No: keep reads explicit for reviewers.

    def rules(c: Counter[str]) -> list[Rule]:
        return [Rule(v, n) for v, n in sorted(c.items())]

    return Policy(job=job, workdir=workdir, observed_runs=len(runs), tools=rules(tools), reads=rules(reads),
                  writes=rules(writes), commands=rules(commands), hosts=rules(hosts),
                  max_writes_per_run=max_writes)


def _q(word: str) -> str:
    return word if re.fullmatch(r"[A-Za-z0-9_@%+:,./~^*-]+", word) else shlex.quote(word)


# ---------------------------------------------------------------- YAML

def _scalar(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)


def render(policy: Policy) -> str:
    lines = [
        f"# Mandate for {policy.job}: generated from {policy.observed_runs} observed run(s).",
        "# Review every rule. Nothing is enforced until `hermes mandate approve`.",
        f"mandate: {FORMAT_VERSION}",
        f"job: {_scalar(policy.job)}",
        f"workdir: {_scalar(policy.workdir)}",
        f"observed_runs: {policy.observed_runs}",
    ]

    def block(name: str, items: list[Rule], indent: str = "") -> None:
        if not items:
            lines.append(f"{indent}{name}: []")
            return
        lines.append(f"{indent}{name}:")
        for r in items:
            lines.append(f"{indent}  - {_scalar(r.value)}  # seen {r.evidence}x")

    block("tools", policy.tools)
    lines.append("paths:")
    block("read", policy.reads, "  ")
    block("write", policy.writes, "  ")
    block("commands", policy.commands)
    block("hosts", policy.hosts)
    lines.append("limits:")
    mw = "null" if policy.max_writes_per_run is None else str(policy.max_writes_per_run)
    lines.append(f"  max_writes_per_run: {mw}")
    return "\n".join(lines) + "\n"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PolicyParseError(ValueError):
    pass


def _parse_value(text: str) -> tuple[str, str]:
    """Return (value, trailing comment) for a list item or scalar."""
    text = text.strip()
    if text.startswith('"'):
        try:
            val, end = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError as exc:
            raise PolicyParseError(f"bad string {text!r}: {exc}") from exc
        return str(val), text[end:].strip()
    val, _, comment = text.partition(" #")
    return val.strip(), ("#" + comment) if comment else ""


def parse(text: str) -> Policy:
    pol = Policy(job="")
    section: str | None = None
    sub: str | None = None
    targets = {"tools": pol.tools, "commands": pol.commands, "hosts": pol.hosts,
               "paths.read": pol.reads, "paths.write": pol.writes}
    saw_version = False
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if line.startswith("- "):
            key = f"{section}.{sub}" if section == "paths" else section
            if key not in targets:
                raise PolicyParseError(f"line {n}: list item outside a rule list")
            val, comment = _parse_value(line[2:])
            m = _SEEN.search(comment)
            targets[key].append(Rule(val, int(m.group(1)) if m else 0))
            continue
        name, sep, rest = line.partition(":")
        if not sep:
            raise PolicyParseError(f"line {n}: expected 'key: value'")
        val, _ = _parse_value(rest) if rest.strip() else ("", "")
        if indent == 0:
            section, sub = name, None
            if name == "mandate":
                saw_version = True
                if val != str(FORMAT_VERSION):
                    raise PolicyParseError(f"unsupported mandate format {val}")
            elif name == "job":
                pol.job = val
            elif name == "workdir":
                pol.workdir = val
            elif name == "observed_runs":
                pol.observed_runs = int(val or 0)
            elif name in ("tools", "commands", "hosts", "paths", "limits"):
                if val not in ("", "[]"):
                    raise PolicyParseError(f"line {n}: {name} must be a list or block")
            else:
                raise PolicyParseError(f"line {n}: unknown key {name!r}")
        elif section == "paths" and name in ("read", "write"):
            sub = name
            if val not in ("", "[]"):
                raise PolicyParseError(f"line {n}: paths.{name} must be a list")
        elif section == "limits" and name == "max_writes_per_run":
            pol.max_writes_per_run = None if val in ("null", "", "~") else int(val)
        else:
            raise PolicyParseError(f"line {n}: unexpected key {name!r}")
    if not saw_version:
        raise PolicyParseError("missing 'mandate:' format line")
    return pol

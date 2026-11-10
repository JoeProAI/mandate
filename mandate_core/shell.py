"""Shell command parser: splits a command line into segments without executing anything.

Any construct that can run code Mandate cannot see (command substitution, subshells,
heredocs, nested interpreters, command-running wrappers, variable or brace expansion)
marks the segment opaque. Opaque segments are only allowed by a verbatim rule.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

SEPARATORS = {";", "&&", "||", "|", "|&", "&", "\n"}
REDIRECT_WRITE = {">", ">>", ">|", "&>", "&>>"}
REDIRECT_READ = {"<"}
SAFE_REDIRECT_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr"}

SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh", "busybox", "rbash"}
INTERPRETERS = {
    "python", "python2", "python3", "pypy", "pypy3", "node", "nodejs", "deno", "bun", "perl", "ruby",
    "php", "lua", "luajit", "tclsh", "osascript", "pwsh", "powershell", "rscript", "julia", "irb",
}
CODE_FLAGS = {"-c", "-e", "-E", "-r", "--eval", "--command", "-p", "--print", "--exec"}
AWK = {"awk", "gawk", "mawk", "nawk"}
WRAPPERS = {
    "eval", "exec", "source", ".", "command", "builtin", "nohup", "timeout", "nice", "ionice", "stdbuf",
    "time", "sudo", "doas", "su", "chroot", "nsenter", "unshare", "setsid", "watch", "flock", "parallel",
    "xargs", "script", "strace", "ltrace", "gdb", "runuser", "pkexec", "systemd-run", "trap", "coproc",
    "expect", "unbuffer", "chpst", "taskset", "fakeroot", "firejail", "bwrap",
}
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
# Heads whose leading positional words name a subcommand; value = how many words to keep.
SUBCOMMAND_DEPTH = {
    "git": 1, "gh": 2, "glab": 2, "docker": 1, "podman": 1, "kubectl": 1, "helm": 1, "npm": 1, "pnpm": 1,
    "yarn": 1, "cargo": 1, "go": 1, "uv": 1, "pip": 1, "pip3": 1, "poetry": 1, "brew": 1, "apt": 1,
    "apt-get": 1, "systemctl": 1, "terraform": 1, "aws": 2, "gcloud": 2, "az": 2, "hermes": 1, "make": 0,
}
# Heads using single-dash long options (do not split -name into -n -a -m -e).
SINGLE_DASH_LONG = {"find", "java", "gcc", "g++", "clang", "go", "ffmpeg", "convert", "openssl", "tar"}
FILE_HEADS = {
    "cat", "head", "tail", "less", "more", "cp", "mv", "rm", "rmdir", "touch", "mkdir", "ln", "chmod",
    "chown", "chgrp", "tee", "ls", "wc", "diff", "stat", "file", "du", "sort", "uniq", "truncate", "shred",
    "tac", "nl", "od", "xxd", "hexdump", "strings", "md5sum", "sha256sum", "base64", "install", "readlink",
    "realpath", "unlink", "dd", "rsync", "tar", "zip", "unzip", "gzip", "gunzip", "jq", "yq", "cut",
}
WRITE_HEADS = {
    "rm", "rmdir", "mv", "cp", "touch", "mkdir", "ln", "chmod", "chown", "chgrp", "tee", "truncate", "shred",
    "install", "unlink", "dd", "rsync",
}
NETWORK_HEADS = {"curl", "wget", "http", "https", "xh", "httpie", "aria2c", "lynx", "w3m"}
REMOTE_HEADS = {"ssh", "scp", "sftp", "rsync", "git", "mosh"}
HOST_ARG_HEADS = {"nc", "ncat", "netcat", "telnet", "ping", "ping6", "dig", "nslookup", "host", "ftp", "socat"}

_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SCP_RE = re.compile(r"^(?:[^@/\s]+@)?(\[[^\]]+\]|[A-Za-z0-9.-]+):(?!//)")


@dataclass(frozen=True)
class Word:
    text: str
    dynamic: bool = False   # contains unquoted/double-quoted $ expansion or brace expansion
    quoted: bool = False


@dataclass
class Segment:
    raw: str
    head: str = ""
    words: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    positionals: tuple[str, ...] = ()
    flag_values: tuple[str, ...] = ()
    redirects: tuple[tuple[str, str], ...] = ()
    opaque: str | None = None
    is_write: bool = False
    cd_target: str | None = None
    extra: dict = field(default_factory=dict)

    def key(self) -> str:
        """Canonical text for a non-opaque segment: env names, command words, sorted flags."""
        parts = [f"{e}=" for e in self.env] + list(self.words) + list(self.flags)
        return " ".join(parts)


class ShellParseError(ValueError):
    pass


def _lex(command: str) -> tuple[list[tuple[str, object]], str | None]:
    """Tokenize into ("word", Word) / ("op", str). Returns (tokens, opaque_reason)."""
    tokens: list[tuple[str, object]] = []
    reason: str | None = None
    i, n = 0, len(command)
    buf: list[str] = []
    in_word = False
    dynamic = quoted = False

    def flush() -> None:
        nonlocal buf, in_word, dynamic, quoted
        if in_word:
            tokens.append(("word", Word("".join(buf), dynamic, quoted)))
        buf, in_word, dynamic, quoted = [], False, False, False

    while i < n:
        c = command[i]
        if c == "\\":
            if i + 1 < n and command[i + 1] == "\n":
                i += 2
                continue
            if i + 1 >= n:
                raise ShellParseError("trailing backslash")
            buf.append(command[i + 1])
            in_word = True
            i += 2
            continue
        if c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                raise ShellParseError("unterminated single quote")
            buf.append(command[i + 1:j])
            in_word = quoted = True
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while True:
                if j >= n:
                    raise ShellParseError("unterminated double quote")
                d = command[j]
                if d == "\\" and j + 1 < n:
                    buf.append(command[j + 1] if command[j + 1] in '"\\$`\n' else command[j:j + 2])
                    j += 2
                    continue
                if d == '"':
                    break
                if d == "`" or (d == "$" and j + 1 < n and command[j + 1] == "("):
                    reason = reason or "command substitution"
                if d == "$" and j + 1 < n and (command[j + 1].isalnum() or command[j + 1] in "_{@*#?!-$"):
                    dynamic = True
                buf.append(d)
                j += 1
            in_word = quoted = True
            i = j + 1
            continue
        if c == "$" and command.startswith("$'", i):
            j = command.find("'", i + 2)
            if j < 0:
                raise ShellParseError("unterminated ANSI-C quote")
            reason = reason or "ANSI-C quoting"
            buf.append(command[i + 2:j])
            in_word = True
            i = j + 1
            continue
        if c == "`" or command.startswith("$(", i):
            reason = reason or "command substitution"
            buf.append(c)
            in_word = True
            dynamic = True
            i += 1
            continue
        if c == "$" and i + 1 < n and (command[i + 1].isalnum() or command[i + 1] in "_{@*#?!-$["):
            dynamic = True
            buf.append(c)
            in_word = True
            i += 1
            continue
        if c in "<>" and i + 1 < n and command[i + 1] == "(":
            reason = reason or "process substitution"
            i += 2
            continue
        if c in "{}" and not quoted:
            # Brace expansion like {a,b} or {1..3} can hide paths.
            if c == "{" and re.match(r"\{[^{}\s]*(,|\.\.)[^{}\s]*\}", command[i:]):
                dynamic = True
            buf.append(c)
            in_word = True
            i += 1
            continue
        if c in " \t":
            flush()
            i += 1
            continue
        if c == "#" and not in_word:
            j = command.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in ";&|\n()<>":
            fd = ""
            if c in "<>" and in_word and not quoted and "".join(buf).isdigit():
                fd = "".join(buf)
                buf, in_word = [], False
            flush()
            for op in ("<<<", "<<-", "&>>", ";;", "&&", "||", "|&", ">>", ">|", "&>", ">&", "<&", "<<", "<>"):
                if command.startswith(op, i):
                    break
            else:
                op = c
            if op in ("<<", "<<-", "<<<"):
                reason = reason or "heredoc or herestring"
            if op in ("(", ")"):
                reason = reason or "subshell"
            if op == ";;":
                reason = reason or "case statement"
            tokens.append(("op", fd + op))
            i += len(op)
            continue
        buf.append(c)
        in_word = True
        i += 1
    flush()
    return tokens, reason


def _split_flag(tok: str, head: str) -> tuple[list[str], str | None]:
    """Return (flags, value) for a flag token."""
    if tok.startswith("--"):
        name, eq, value = tok.partition("=")
        return [name], (value if eq else None)
    if head in SINGLE_DASH_LONG or len(tok) == 2:
        name, eq, value = tok.partition("=")
        return [name], (value if eq else None)
    letters = []
    rest = tok[1:]
    for idx, ch in enumerate(rest):
        if ch.isalpha():
            letters.append("-" + ch)
        else:
            return letters or [tok], rest[idx:].lstrip("=") or None
    return letters, None


def _analyze(raw: str, words: list[Word], redirects: list[tuple[str, str]], opaque: str | None) -> Segment:
    seg = Segment(raw=raw.strip(), redirects=tuple(redirects))
    env: list[str] = []
    idx = 0
    while idx < len(words) and _ASSIGN_RE.match(words[idx].text) and not words[idx].quoted:
        env.append(words[idx].text.split("=", 1)[0])
        idx += 1
    rest = words[idx:]
    if rest and rest[0].text == "env":
        k = 1
        while k < len(rest):
            t = rest[k].text
            if t == "--":
                k += 1
                break
            if t.startswith("-"):
                if t in ("-u", "--unset", "-C", "--chdir"):
                    k += 2
                    continue
                opaque = opaque or "env option"
                k += 1
                continue
            if _ASSIGN_RE.match(t):
                env.append(t.split("=", 1)[0])
                k += 1
                continue
            break
        rest = rest[k:]
        seg.extra["env_wrapper"] = True
    seg.env = tuple(sorted(set(env)))
    if not rest:
        seg.opaque = opaque
        return seg
    if any(w.dynamic for w in rest):
        opaque = opaque or "variable or brace expansion"
    head_text = rest[0].text
    seg.head = head_text
    base = posixpath.basename(head_text)
    lname = base.lower()
    pyname = re.sub(r"[0-9.]+$", "", lname)
    if lname in SHELLS:
        opaque = opaque or f"nested shell ({base})"
    elif lname in WRAPPERS:
        opaque = opaque or f"{base} runs another command"
    elif lname in AWK:
        opaque = opaque or f"{base} program is code"
    elif lname in INTERPRETERS or pyname in INTERPRETERS:
        texts = [w.text for w in rest[1:]]
        if not texts or any(t in CODE_FLAGS or (t.startswith("-") and not t.startswith("--") and t[1:2] in "ceEr"
                                                 and len(t) > 1) for t in texts) or texts[0] == "-":
            opaque = opaque or f"inline code for {base}"
    flags: list[str] = []
    flag_values: list[str] = []
    positionals: list[str] = []
    end_of_flags = False
    for w in rest[1:]:
        t = w.text
        if not end_of_flags and t == "--":
            end_of_flags = True
            continue
        if not end_of_flags and t.startswith("-") and t != "-":
            names, value = _split_flag(t, lname)
            flags.extend(names)
            if value:
                flag_values.append(value)
            if lname == "find" and t in FIND_EXEC:
                opaque = opaque or "find -exec runs another command"
            continue
        positionals.append(t)
    depth = SUBCOMMAND_DEPTH.get(lname, 0)
    sub = [p for p in positionals[:depth]]
    seg.words = (head_text, *sub)
    seg.positionals = tuple(positionals[depth:])
    seg.flags = tuple(sorted(set(flags)))
    seg.flag_values = tuple(flag_values)
    if lname == "sed" and any(f in ("-i", "--in-place") for f in flags):
        seg.is_write = True
    if lname in WRITE_HEADS or any(op.lstrip("0123456789") in REDIRECT_WRITE and tgt not in SAFE_REDIRECT_TARGETS
                                   for op, tgt in redirects):
        seg.is_write = True
    if lname in ("cd", "pushd"):
        seg.cd_target = positionals[0] if positionals else "~"
    seg.opaque = opaque
    return seg


def parse(command: str) -> list[Segment]:
    """Split *command* into analysed segments. Never raises: parse errors yield one opaque segment."""
    if not isinstance(command, str):
        return [Segment(raw=str(command)[:200], opaque="command is not a string")]
    if "\x00" in command:
        return [Segment(raw=command.replace("\x00", "\\0"), opaque="NUL byte in command")]
    try:
        tokens, global_reason = _lex(command)
    except ShellParseError as exc:
        return [Segment(raw=command, opaque=f"unparseable: {exc}")]
    segments: list[Segment] = []
    words: list[Word] = []
    redirects: list[tuple[str, str]] = []
    raw_parts: list[str] = []
    pending_redirect: str | None = None
    opaque: str | None = None
    piped_from_previous = False

    def close() -> None:
        nonlocal words, redirects, raw_parts, opaque
        if words or redirects or opaque:
            seg = _analyze(" ".join(raw_parts), words, redirects, opaque)
            seg.extra["piped"] = piped_from_previous
            segments.append(seg)
        words, redirects, raw_parts, opaque = [], [], [], None

    for kind, val in tokens:
        if kind == "op":
            op = str(val)
            bare = op.lstrip("0123456789")
            if bare in SEPARATORS:
                if pending_redirect:
                    opaque = opaque or "redirect without target"
                    pending_redirect = None
                close()
                piped_from_previous = bare in ("|", "|&")
                continue
            if bare in ("<<", "<<-", "<<<", "(", ")", ";;"):
                opaque = opaque or {"(": "subshell", ")": "subshell", ";;": "case statement"}.get(
                    bare, "heredoc or herestring")
                raw_parts.append(op)
                continue
            pending_redirect = op
            raw_parts.append(op)
            continue
        w = val  # type: ignore[assignment]
        assert isinstance(w, Word)
        raw_parts.append(_quote_for_display(w.text))
        if pending_redirect is not None:
            bare = pending_redirect.lstrip("0123456789")
            if bare in (">&", "<&") and (w.text.isdigit() or w.text == "-"):
                pass
            else:
                if w.dynamic:
                    opaque = opaque or "variable in redirect target"
                redirects.append((pending_redirect if bare not in (">&",) else ">", w.text))
            pending_redirect = None
            continue
        words.append(w)
    if pending_redirect:
        opaque = opaque or "redirect without target"
    close()
    if global_reason:
        # Substitution/heredoc anywhere makes every segment untrustworthy as a parsed unit.
        if len(segments) == 1:
            segments[0].opaque = segments[0].opaque or global_reason
        else:
            for s in segments:
                s.opaque = s.opaque or global_reason
            for s in segments:
                s.raw = s.raw
    if not segments:
        return []
    return segments


def _quote_for_display(text: str) -> str:
    if text and re.fullmatch(r"[A-Za-z0-9_@%+=:,./~^-]+", text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def host_candidates(seg: Segment) -> list[str]:
    """URL-ish strings in a segment that name a network destination."""
    out: list[str] = []
    lname = posixpath.basename(seg.head).lower()
    values = list(seg.positionals) + list(seg.flag_values) + [t for _, t in seg.redirects]
    for v in values:
        if _URL_RE.match(v):
            out.append(v)
    if lname in NETWORK_HEADS:
        for v in seg.positionals:
            if not _URL_RE.match(v) and v and not v.startswith(("-", "/", ".", "~")) and ("." in v or ":" in v) \
                    and not any(c.isspace() for c in v):
                out.append("http://" + v)
    if lname in REMOTE_HEADS:
        for v in seg.positionals:
            if _URL_RE.match(v):
                continue
            m = _SCP_RE.match(v)
            if m:
                out.append("ssh://" + m.group(1))
            elif lname in ("ssh", "mosh") and v and not v.startswith(("-", "/", ".")):
                out.append("ssh://" + v.split()[0])
                break
    if lname in HOST_ARG_HEADS:
        for v in seg.positionals:
            if v and not v.startswith("-"):
                out.append("tcp://" + v)
                break
    return out


def path_candidates(seg: Segment) -> list[tuple[str, str]]:
    """(op, path) pairs a segment touches: op is "read" or "write"."""
    out: list[tuple[str, str]] = []
    lname = posixpath.basename(seg.head).lower()
    write = lname in WRITE_HEADS or (lname == "sed" and seg.is_write)
    values = list(seg.positionals) + list(seg.flag_values)
    for idx, v in enumerate(values):
        if not v or _URL_RE.match(v):
            continue
        escapeish = v.startswith(("/", "~")) or v == ".." or v.startswith("../") or "/../" in v or v.endswith("/..")
        file_arg = lname in FILE_HEADS or (lname == "sed" and idx > 0)
        if file_arg or escapeish:
            out.append(("write" if write else "read", v))
    for op, tgt in seg.redirects:
        if tgt in SAFE_REDIRECT_TARGETS:
            continue
        bare = op.lstrip("0123456789")
        out.append(("read" if bare in REDIRECT_READ else "write", tgt))
    if seg.cd_target is not None:
        out.append(("read", seg.cd_target))
    if posixpath.dirname(seg.head) and not seg.opaque:
        out.append(("read", seg.head))
    return out

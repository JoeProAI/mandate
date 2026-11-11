"""Learn observations from a job's existing Hermes run history (`propose --learn-from`).

Source grammar:
  history          the job's own cron history (same as cron:<job>)
  cron:<name|id>   a cron job from $HERMES_HOME/cron/jobs.json, matched by id first, then name
  <path>           a session transcript (.jsonl) or a directory of them

Cron runs are found as sessions named cron_<job_id>_* in $HERMES_HOME/sessions/*.jsonl and in
$HERMES_HOME/state.db. cron/output/<job_id>/*.md and cron/executions.db hold no tool calls; they are
only counted so a thin import can say how many runs Hermes knows about but left no transcript.
Nothing here writes to the Hermes data dir.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GRAMMAR = "history, cron:<job name or id>, or a path to a session .jsonl file or a directory of them"


class HistoryError(Exception):
    pass


@dataclass
class LearnedRun:
    session_id: str
    started_at: float
    calls: list[tuple[str, Any]]

    @property
    def run_id(self) -> str:
        return f"history:{self.session_id}"


@dataclass
class History:
    label: str
    job_id: str | None = None
    job_name: str | None = None
    workdir: str | None = None
    runs: list[LearnedRun] = field(default_factory=list)
    skipped_rows: int = 0
    skipped_calls: int = 0
    blocked_calls: int = 0
    ledger_runs: int | None = None
    output_runs: int | None = None
    looked_in: list[str] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        return sum(len(r.calls) for r in self.runs)


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        return Path("~/.hermes").expanduser()


def load_cron_jobs(home: Path) -> list[dict]:
    path = home / "cron" / "jobs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and "jobs" in data:
        data = data["jobs"]
    if isinstance(data, dict):
        items = [dict(v, id=v.get("id") or k) for k, v in sorted(data.items()) if isinstance(v, dict)]
    elif isinstance(data, list):
        items = [v for v in data if isinstance(v, dict)]
    else:
        items = []
    return [j for j in items if j.get("id")]


def find_cron_job(home: Path, ref: str) -> dict | None:
    jobs = load_cron_jobs(home)
    for j in jobs:
        if str(j["id"]) == ref:
            return j
    named = [j for j in jobs if str(j.get("name") or "") == ref]
    if len(named) > 1:
        ids = ", ".join(sorted(str(j["id"]) for j in named))
        raise HistoryError(f"more than one cron job is named {ref!r} ({ids}); use --learn-from cron:<id>")
    return named[0] if named else None


def _decode_args(raw: Any) -> dict | None:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def calls_from_messages(messages: list[dict], hist: History) -> list[tuple[str, Any]]:
    """Tool calls an assistant actually made. Malformed calls and calls Mandate blocked are skipped."""
    blocked = set()
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id") and "MANDATE BLOCKED" in str(m.get("content") or ""):
            blocked.add(str(m["tool_call_id"]))
    out: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for m in messages:
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        tcs = m["tool_calls"]
        if isinstance(tcs, str):
            try:
                tcs = json.loads(tcs)
            except ValueError:
                hist.skipped_rows += 1
                continue
        if not isinstance(tcs, list):
            hist.skipped_rows += 1
            continue
        for tc in tcs:
            fn = tc.get("function") if isinstance(tc, dict) else None
            fn = fn if isinstance(fn, dict) else tc if isinstance(tc, dict) else {}
            name = fn.get("name")
            args = _decode_args(fn.get("arguments"))
            if not isinstance(name, str) or not name or args is None:
                hist.skipped_calls += 1
                continue
            tcid = str(tc.get("id") or "") if isinstance(tc, dict) else ""
            if tcid and tcid in seen:
                continue
            if tcid:
                seen.add(tcid)
            if tcid in blocked:
                hist.blocked_calls += 1
                continue
            out.append((name, args))
    return out


def read_jsonl(path: Path, hist: History) -> list[dict]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HistoryError(f"cannot read {path}: {exc.strerror or exc}") from None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            hist.skipped_rows += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            hist.skipped_rows += 1
    return rows


def _first_ts(rows: list[dict]) -> float:
    for r in rows:
        ts = r.get("timestamp")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            return float(ts)
    return 0.0


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _state_db_sessions(home: Path, prefix: str, hist: History) -> dict[str, tuple[float, list[dict]]]:
    conn = _ro(home / "state.db")
    if conn is None:
        return {}
    hist.looked_in.append(str(home / "state.db"))
    out: dict[str, tuple[float, list[dict]]] = {}
    try:
        ids = conn.execute("SELECT id, started_at FROM sessions WHERE substr(id, 1, ?) = ? ORDER BY id",
                           (len(prefix), prefix)).fetchall()
        for row in ids:
            msgs = conn.execute("SELECT role, content, tool_call_id, tool_calls, tool_name, timestamp FROM messages "
                                "WHERE session_id = ? ORDER BY id", (row["id"],)).fetchall()
            started = row["started_at"] if isinstance(row["started_at"], (int, float)) else 0.0
            out[row["id"]] = (float(started or 0.0), [dict(m) for m in msgs])
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return out


def _ledger_count(home: Path, job_id: str) -> int | None:
    conn = _ro(home / "cron" / "executions.db")
    if conn is None:
        return None
    try:
        return int(conn.execute("SELECT COUNT(*) FROM executions WHERE job_id = ?", (job_id,)).fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _add_run(hist: History, sid: str, started: float, rows: list[dict]) -> None:
    calls = calls_from_messages(rows, hist)
    if calls:
        hist.runs.append(LearnedRun(sid, started or _first_ts(rows), calls))


def from_cron(home: Path, ref: str, label: str) -> History:
    job = find_cron_job(home, ref)
    job_id = str(job["id"]) if job else ref
    hist = History(label=label, job_id=job_id if job else None, job_name=str(job.get("name") or job_id) if job else None,
                   workdir=(job.get("workdir") or None) if job else None)
    prefix = f"cron_{job_id}_"
    sessions: dict[str, tuple[float, list[dict]]] = _state_db_sessions(home, prefix, hist)
    sdir = home / "sessions"
    hist.looked_in.append(str(sdir / f"{prefix}*.jsonl"))
    if sdir.is_dir():
        for path in sorted(sdir.glob("*.jsonl")):
            if not path.stem.startswith(prefix):
                continue
            rows = read_jsonl(path, hist)
            started, prior = sessions.get(path.stem, (0.0, []))
            sessions[path.stem] = (started, prior + rows)
    for sid in sorted(sessions):
        started, rows = sessions[sid]
        _add_run(hist, sid, started, rows)
    hist.ledger_runs = _ledger_count(home, job_id)
    out_dir = home / "cron" / "output" / job_id
    hist.output_runs = len(list(out_dir.glob("*.md"))) if out_dir.is_dir() else None
    return hist


def from_path(path: Path, label: str) -> History:
    hist = History(label=label)
    if path.is_dir():
        files = sorted(path.glob("*.jsonl"))
    elif path.is_file():
        if path.suffix == ".md":
            raise HistoryError(f"{path} is cron output (prompt and response only, no tool calls); "
                               "use --learn-from history or point at the session .jsonl")
        files = [path]
    else:
        raise HistoryError(f"{path} does not exist (--learn-from takes {GRAMMAR})")
    hist.looked_in.append(str(path))
    for f in files:
        rows = read_jsonl(f, hist)
        _add_run(hist, f.stem, 0.0, rows)
    return hist


def resolve(source: str, job_ref: str, bound_job_id: str | None = None, home: Path | None = None) -> History:
    """Load learned runs for *source*. *bound_job_id* is the job id Mandate already bound, if any."""
    home = home or hermes_home()
    src = (source or "").strip()
    if src == "history":
        ref = bound_job_id if bound_job_id and not bound_job_id.startswith("name:") else job_ref
        if find_cron_job(home, ref) is None and ref != job_ref:
            ref = job_ref
        return from_cron(home, ref, f"history ({home})")
    if src.startswith("cron:"):
        ref = src[5:].strip()
        if not ref:
            raise HistoryError("cron: needs a job name or id, e.g. --learn-from cron:nightly-triage")
        return from_cron(home, ref, f"cron:{ref} ({home})")
    if src and ("/" in src or src.startswith(("~", ".")) or src.endswith(".jsonl") or Path(src).exists()):
        path = Path(src).expanduser()
        return from_path(path, str(path))
    raise HistoryError(f"unknown --learn-from source {source!r}; use {GRAMMAR}")

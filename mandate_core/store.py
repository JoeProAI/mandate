"""SQLite store under the plugin's own data dir: jobs, runs, observations, policies, drift."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, job_name TEXT, mode TEXT, active_version INTEGER,
  watched INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  job_id TEXT NOT NULL, run_id TEXT NOT NULL, started_at REAL NOT NULL, mode TEXT,
  calls INTEGER NOT NULL DEFAULT 0, writes INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (job_id, run_id));
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, run_id TEXT NOT NULL, ts REAL NOT NULL,
  tool TEXT NOT NULL, shape TEXT NOT NULL, outcome TEXT NOT NULL, tool_call_id TEXT, status TEXT);
CREATE INDEX IF NOT EXISTS obs_job ON observations(job_id, run_id);
CREATE TABLE IF NOT EXISTS policies (
  job_id TEXT NOT NULL, version INTEGER NOT NULL, yaml TEXT NOT NULL, sha256 TEXT NOT NULL,
  created_at REAL NOT NULL, approved_at REAL, source TEXT NOT NULL, note TEXT,
  PRIMARY KEY (job_id, version));
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, version INTEGER, action TEXT NOT NULL,
  ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS drift (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, run_id TEXT NOT NULL, ts REAL NOT NULL,
  tool TEXT NOT NULL, call TEXT NOT NULL, family TEXT NOT NULL, subject TEXT NOT NULL, reason TEXT NOT NULL,
  policy_version INTEGER, action TEXT NOT NULL, shape TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open');
CREATE INDEX IF NOT EXISTS drift_job ON drift(job_id);
CREATE TABLE IF NOT EXISTS scopes (
  session_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, started_at REAL NOT NULL);
"""


def default_db_path() -> Path:
    override = os.environ.get("MANDATE_DB")
    if override:
        return Path(override)
    try:
        from plugins.plugin_storage import plugin_data_dir  # documented Hermes plugin storage API

        return plugin_data_dir("mandate") / "mandate.db"
    except ImportError:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        d = home / "plugin-data" / "mandate"
        d.mkdir(parents=True, exist_ok=True)
        return d / "mandate.db"


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        with self._init_lock:
            c = self._conn()
            c.executescript(SCHEMA)
            for table in ("runs", "observations"):
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if "source" not in cols:
                    # 'hook' = seen live by pre_tool_call; 'history' = learned from Hermes run history.
                    c.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT NOT NULL DEFAULT 'hook'")

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            try:
                c.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            c.execute("PRAGMA busy_timeout=5000")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self._conn().execute(sql, params))

    def x(self, sql: str, params: tuple = ()) -> int:
        cur = self._conn().execute(sql, params)
        return cur.lastrowid or 0

    # ---------------------------------------------------------------- jobs

    def job(self, ref: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM jobs WHERE job_id=?", (ref,))
        if rows:
            return rows[0]
        rows = self.q("SELECT * FROM jobs WHERE job_name=? ORDER BY created_at", (ref,))
        return rows[0] if len(rows) == 1 else (rows[0] if rows else None)

    def jobs(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM jobs ORDER BY job_name, job_id")

    def bind_job(self, job_id: str, job_name: str | None) -> sqlite3.Row:
        """Row for a running job; adopts a `watch <name>` placeholder the first time the name runs."""
        row = self.q("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if row:
            return row[0]
        if job_name:
            placeholder = self.q("SELECT * FROM jobs WHERE job_id=? AND job_name=?", (f"name:{job_name}", job_name))
            if placeholder:
                self.x("UPDATE jobs SET job_id=? WHERE job_id=?", (job_id, f"name:{job_name}"))
                for table in ("runs", "observations", "policies", "approvals", "drift", "scopes"):
                    self.x(f"UPDATE {table} SET job_id=? WHERE job_id=?", (job_id, f"name:{job_name}"))
                return self.q("SELECT * FROM jobs WHERE job_id=?", (job_id,))[0]
        self.x("INSERT OR IGNORE INTO jobs(job_id, job_name, mode, active_version, watched, created_at) "
               "VALUES (?,?,NULL,NULL,0,?)", (job_id, job_name, time.time()))
        return self.q("SELECT * FROM jobs WHERE job_id=?", (job_id,))[0]

    def watch(self, ref: str) -> sqlite3.Row:
        row = self.job(ref)
        if row is None:
            self.x("INSERT INTO jobs(job_id, job_name, mode, active_version, watched, created_at) "
                   "VALUES (?,?,?,NULL,1,?)", (f"name:{ref}", ref, "observe", time.time()))
            return self.q("SELECT * FROM jobs WHERE job_id=?", (f"name:{ref}",))[0]
        self.x("UPDATE jobs SET watched=1, mode=COALESCE(mode,'observe') WHERE job_id=?", (row["job_id"],))
        return self.q("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],))[0]

    def set_mode(self, job_id: str, mode: str) -> None:
        self.x("UPDATE jobs SET mode=? WHERE job_id=?", (mode, job_id))

    # ---------------------------------------------------------------- runs & observations

    def touch_run(self, job_id: str, run_id: str, mode: str) -> sqlite3.Row:
        self.x("INSERT OR IGNORE INTO runs(job_id, run_id, started_at, mode) VALUES (?,?,?,?)",
               (job_id, run_id, time.time(), mode))
        return self.q("SELECT * FROM runs WHERE job_id=? AND run_id=?", (job_id, run_id))[0]

    def record(self, job_id: str, run_id: str, shape: dict, outcome: str, tool_call_id: str | None,
               writes: int) -> int:
        blocked = 1 if outcome == "blocked" else 0
        self.x("UPDATE runs SET calls=calls+1, writes=writes+?, blocked=blocked+? WHERE job_id=? AND run_id=?",
               (0 if blocked else writes, blocked, job_id, run_id))
        return self.x("INSERT INTO observations(job_id, run_id, ts, tool, shape, outcome, tool_call_id) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (job_id, run_id, time.time(), shape["tool"], json.dumps(shape, sort_keys=True), outcome,
                       tool_call_id))

    def run_writes(self, job_id: str, run_id: str) -> tuple[set[str], int]:
        """(inside paths written, write calls made) by the not-blocked calls of one run so far."""
        paths: set[str] = set()
        calls = 0
        for row in self.q("SELECT shape FROM observations WHERE job_id=? AND run_id=? AND outcome IN "
                          "('observed','allowed','would_block')", (job_id, run_id)):
            d = json.loads(row["shape"])
            if int(d.get("write_count", 0)):
                calls += 1
                paths.update(v for v, inside, ok in d.get("writes", []) if inside and ok)
        return paths, calls

    def mark_status(self, tool_call_id: str, status: str) -> None:
        self.x("UPDATE observations SET status=? WHERE tool_call_id=? AND status IS NULL", (status, tool_call_id))

    def replace_history(self, job_id: str, runs: list[tuple[str, float, list[dict]]]) -> None:
        """Store runs learned from Hermes history as source='history'; re-learning a run replaces it."""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            for run_id, started_at, shapes in runs:
                c.execute("DELETE FROM observations WHERE job_id=? AND run_id=? AND source='history'", (job_id, run_id))
                c.execute("DELETE FROM runs WHERE job_id=? AND run_id=? AND source='history'", (job_id, run_id))
                writes = sum(int(d.get("write_count", 0)) for d in shapes)
                c.execute("INSERT INTO runs(job_id, run_id, started_at, mode, calls, writes, source) "
                          "VALUES (?,?,?,?,?,?,'history')", (job_id, run_id, started_at, "history", len(shapes), writes))
                for d in shapes:
                    c.execute("INSERT INTO observations(job_id, run_id, ts, tool, shape, outcome, source) "
                              "VALUES (?,?,?,?,?,'observed','history')",
                              (job_id, run_id, started_at, d["tool"], json.dumps(d, sort_keys=True)))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def runs(self, job_id: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM runs WHERE job_id=? ORDER BY started_at, run_id", (job_id,))

    def observations(self, job_id: str, outcomes: tuple[str, ...] = ("observed", "allowed", "would_block")
                     ) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(outcomes))
        return self.q(f"SELECT * FROM observations WHERE job_id=? AND outcome IN ({marks}) ORDER BY id",
                      (job_id, *outcomes))

    # ---------------------------------------------------------------- policies

    def add_policy(self, job_id: str, yaml_text: str, sha: str, source: str, note: str = "") -> int:
        version = (self.q("SELECT MAX(version) AS v FROM policies WHERE job_id=?", (job_id,))[0]["v"] or 0) + 1
        self.x("INSERT INTO policies(job_id, version, yaml, sha256, created_at, source, note) VALUES (?,?,?,?,?,?,?)",
               (job_id, version, yaml_text, sha, time.time(), source, note))
        return version

    def policy(self, job_id: str, version: int) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM policies WHERE job_id=? AND version=?", (job_id, version))
        return rows[0] if rows else None

    def latest_pending(self, job_id: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM policies WHERE job_id=? AND approved_at IS NULL ORDER BY version DESC LIMIT 1",
                      (job_id,))
        return rows[0] if rows else None

    def active_policy(self, job_id: str) -> sqlite3.Row | None:
        job = self.q("SELECT active_version FROM jobs WHERE job_id=?", (job_id,))
        if not job or job[0]["active_version"] is None:
            return None
        return self.policy(job_id, job[0]["active_version"])

    def approve(self, job_id: str, version: int) -> None:
        self.x("UPDATE policies SET approved_at=COALESCE(approved_at, ?) WHERE job_id=? AND version=?",
               (time.time(), job_id, version))
        self.x("UPDATE jobs SET active_version=? WHERE job_id=?", (version, job_id))
        self.x("INSERT INTO approvals(job_id, version, action, ts) VALUES (?,?,?,?)",
               (job_id, version, "approve", time.time()))

    def undo(self, job_id: str) -> int | None:
        """Re-activate the version that was active before the current one (None if there was none)."""
        history = [r["version"] for r in self.q(
            "SELECT version, action FROM approvals WHERE job_id=? ORDER BY id", (job_id,)) if r["action"] != "undo"]
        undos = len(self.q("SELECT id FROM approvals WHERE job_id=? AND action='undo'", (job_id,)))
        stack = history[: max(0, len(history) - undos)]
        if not stack:
            return None
        stack.pop()
        prev = stack[-1] if stack else None
        self.x("UPDATE jobs SET active_version=? WHERE job_id=?", (prev, job_id))
        self.x("INSERT INTO approvals(job_id, version, action, ts) VALUES (?,?,?,?)",
               (job_id, prev, "undo", time.time()))
        if prev is None:
            self.x("UPDATE jobs SET mode='observe' WHERE job_id=?", (job_id,))
        return prev

    # ---------------------------------------------------------------- drift

    def add_drift(self, job_id: str, run_id: str, tool: str, call: str, family: str, subject: str, reason: str,
                  version: int | None, action: str, shape: dict) -> int:
        return self.x("INSERT INTO drift(job_id, run_id, ts, tool, call, family, subject, reason, policy_version, "
                      "action, shape) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (job_id, run_id, time.time(), tool, call, family, subject, reason, version, action,
                       json.dumps(shape, sort_keys=True)))

    def drift(self, job_id: str, limit: int = 200) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM drift WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))

    def drift_item(self, job_id: str, drift_id: int) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM drift WHERE job_id=? AND id=?", (job_id, drift_id))
        return rows[0] if rows else None

    def set_drift_status(self, drift_id: int, status: str) -> None:
        self.x("UPDATE drift SET status=? WHERE id=?", (status, drift_id))

    # ---------------------------------------------------------------- interactive scopes

    def scope_for(self, session_id: str) -> str | None:
        rows = self.q("SELECT job_id FROM scopes WHERE session_id=?", (session_id,))
        return rows[0]["job_id"] if rows else None

    def set_scope(self, session_id: str, job_id: str | None) -> None:
        if job_id is None:
            self.x("DELETE FROM scopes WHERE session_id=?", (session_id,))
        else:
            self.x("INSERT OR REPLACE INTO scopes(session_id, job_id, started_at) VALUES (?,?,?)",
                   (session_id, job_id, time.time()))

    def counts(self) -> dict[str, Any]:
        return {t: self.q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
                for t in ("jobs", "runs", "observations", "policies", "drift")}

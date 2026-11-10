"""Read-only backend for the Mandate Desktop page, mounted by Hermes at /api/plugins/mandate/.

Self-contained on purpose: Hermes imports this file on its own, outside the plugin package. It opens
Mandate's SQLite file read-only and never writes, so the Desktop page can look but not change policy.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

TRACKS = ("tools", "paths", "commands", "hosts")
FAMILY_TRACK = {"tools": "tools", "paths.read": "paths", "paths.write": "paths", "commands": "commands",
                "hosts": "hosts", "limits": "paths"}
_SEEN = re.compile(r"seen (\d+)x")


def db_path() -> Path:
    override = os.environ.get("MANDATE_DB")
    if override:
        return Path(override)
    try:
        from plugins.plugin_storage import plugin_data_dir

        return plugin_data_dir("mandate") / "mandate.db"
    except ImportError:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "plugin-data" / "mandate" / "mandate.db"


def _connect() -> sqlite3.Connection | None:
    path = db_path()
    if not path.exists():
        return None
    c = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    c.row_factory = sqlite3.Row
    return c


def _value(text: str) -> tuple[str, str]:
    text = text.strip()
    if text.startswith('"'):
        dec = json.JSONDecoder()
        val, end = dec.raw_decode(text)
        return str(val), text[end:]
    val, _, comment = text.partition("#")
    return val.strip(), comment


def parse_rules(yaml_text: str) -> dict[str, list[dict]]:
    """Rules per family from Mandate's own deterministic policy format."""
    out: dict[str, list[dict]] = {"tools": [], "paths.read": [], "paths.write": [], "commands": [], "hosts": []}
    section = sub = None
    for raw in (yaml_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            key = f"{section}.{sub}" if section == "paths" else section
            if key in out:
                val, comment = _value(line[2:])
                m = _SEEN.search(comment)
                out[key].append({"value": val, "evidence": int(m.group(1)) if m else 0})
            continue
        name = line.partition(":")[0]
        if raw[:1] != " ":
            section, sub = name, None
        elif section == "paths":
            sub = name
    return out


def _track_rules(rules: dict[str, list[dict]], track: str) -> list[dict]:
    if track == "paths":
        return rules["paths.read"] + rules["paths.write"]
    return rules[track]


def _default_mode() -> str:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        mode = (((cfg.get("plugins") or {}).get("entries") or {}).get("mandate") or {}).get("settings", {}).get("mode")
        return mode if mode in ("observe", "enforce") else "observe"
    except Exception:
        return "observe"


def _job_row(c: sqlite3.Connection, ref: str):
    return c.execute("SELECT * FROM jobs WHERE job_id = ? OR job_name = ? ORDER BY job_name = ? DESC LIMIT 1",
                     (ref, ref, ref)).fetchone()


def jobs_payload() -> dict:
    c = _connect()
    if c is None:
        return {"jobs": [], "db": "missing"}
    try:
        rows = c.execute("SELECT * FROM jobs ORDER BY COALESCE(job_name, job_id)").fetchall()
        default = _default_mode()
        jobs = []
        for j in rows:
            runs = c.execute("SELECT COUNT(*) FROM runs WHERE job_id = ?", (j["job_id"],)).fetchone()[0]
            open_drift = c.execute("SELECT COUNT(*) FROM drift WHERE job_id = ? AND status = 'open'",
                                   (j["job_id"],)).fetchone()[0]
            pending = c.execute("SELECT MAX(version) FROM policies WHERE job_id = ? AND approved_at IS NULL "
                                "AND version > COALESCE(?, 0)", (j["job_id"], j["active_version"])).fetchone()[0]
            jobs.append({"id": j["job_id"], "name": j["job_name"] or j["job_id"], "mode": j["mode"] or default,
                         "version": j["active_version"], "pending_version": pending, "runs": runs,
                         "open_drift": open_drift, "watched": bool(j["watched"])})
        return {"jobs": jobs, "db": "ok"}
    finally:
        c.close()


def job_payload(ref: str) -> dict | None:
    c = _connect()
    if c is None:
        return None
    try:
        j = _job_row(c, ref)
        if j is None:
            return None
        jid = j["job_id"]
        active = (c.execute("SELECT * FROM policies WHERE job_id = ? AND version = ?", (jid, j["active_version"]))
                  .fetchone() if j["active_version"] is not None else None)
        pending = c.execute("SELECT * FROM policies WHERE job_id = ? AND approved_at IS NULL AND version > ? "
                            "ORDER BY version DESC LIMIT 1", (jid, j["active_version"] or 0)).fetchone()
        drift = c.execute("SELECT id, run_id, ts, tool, call, family, subject, reason, policy_version, action, "
                          "status FROM drift WHERE job_id = ? ORDER BY id DESC LIMIT 200", (jid,)).fetchall()
        runs = c.execute("SELECT COUNT(*) FROM runs WHERE job_id = ?", (jid,)).fetchone()[0]
        mode = j["mode"] or _default_mode()
        a_rules = parse_rules(active["yaml"]) if active else parse_rules("")
        p_rules = parse_rules(pending["yaml"]) if pending else parse_rules("")
        tracks = []
        for t in TRACKS:
            allowed = _track_rules(a_rules, t)
            have = {r["value"] for r in allowed}
            tracks.append({
                "name": t,
                "allowed": allowed,
                "pending": [r for r in _track_rules(p_rules, t) if r["value"] not in have],
                "notches": [{"id": d["id"], "status": d["status"], "call": d["call"], "reason": d["reason"]}
                            for d in sorted(drift, key=lambda d: d["id"]) if FAMILY_TRACK.get(d["family"], "commands") == t],
            })
        diff = ""
        if pending is not None:
            diff = "".join(difflib.unified_diff(
                (active["yaml"] if active else "").splitlines(True), pending["yaml"].splitlines(True),
                f"v{active['version']}" if active else "none", f"v{pending['version']} (pending)", n=1))
        return {
            "job": {"id": jid, "name": j["job_name"] or jid, "mode": mode, "runs": runs},
            "enforcing": mode == "enforce" and active is not None,
            "active": ({"version": active["version"], "sha256": active["sha256"], "yaml": active["yaml"],
                        "approved_at": active["approved_at"]} if active else None),
            "pending": ({"version": pending["version"], "yaml": pending["yaml"]} if pending else None),
            "diff": diff,
            "tracks": tracks,
            "drift": [dict(d) for d in drift],
        }
    finally:
        c.close()


@router.get("/jobs")
def list_jobs() -> dict:
    return jobs_payload()


@router.get("/jobs/{ref}")
def get_job(ref: str) -> dict:
    data = job_payload(ref)
    if data is None:
        raise HTTPException(status_code=404, detail=f"unknown job {ref!r}")
    return data

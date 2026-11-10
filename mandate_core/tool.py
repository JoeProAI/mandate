"""Agent-facing `mandate_status` tool and the `/mandate` slash command (both read-only except scope)."""

from __future__ import annotations

import json
from typing import Any

from .engine import Engine


def _job_for(eng: Engine, ref: str | None, session_id: str | None = None):
    store = eng.store
    if ref:
        return store.job(ref)
    sc = eng.scope({"session_id": session_id} if session_id else {})
    return store.job(sc.job_id) if sc else None


def status(eng: Engine, args: dict, **kwargs: Any) -> str:
    try:
        job = _job_for(eng, (args or {}).get("job"), kwargs.get("session_id"))
        if job is None:
            return json.dumps({"mandate": None, "message": "No Mandate job is bound to this run. Nothing is enforced."})
        store = eng.store
        row = store.active_policy(job["job_id"])
        mode = job["mode"] or eng.default_mode()
        sc = eng.scope({"session_id": kwargs.get("session_id")} if kwargs.get("session_id") else {})
        drift = store.drift(job["job_id"], 50)
        if sc is not None and sc.job_id == job["job_id"]:
            drift = [d for d in drift if d["run_id"] == sc.run_id]
        return json.dumps({
            "job": job["job_name"] or job["job_id"],
            "mode": mode,
            "enforcing": mode == "enforce" and row is not None,
            "version": row["version"] if row else None,
            "sha256": row["sha256"] if row else None,
            "mandate_yaml": row["yaml"] if row else None,
            "blocked_this_run": [{"drift_id": d["id"], "call": d["call"], "rule": d["family"], "reason": d["reason"]}
                                 for d in drift[:10]],
            "advice": ("Calls outside the mandate are refused. Do the work the mandate allows and report what "
                       "was blocked; a human can widen the mandate with `hermes mandate widen`."),
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"mandate_status failed: {type(exc).__name__}"})


def slash(eng: Engine, raw: str, session_id: str | None = None, **kwargs: Any) -> str:
    from .cli import status_text, drift_text

    parts = (raw or "").split()
    sub = parts[0] if parts else "status"
    ref = parts[1] if len(parts) > 1 else None
    store = eng.store
    if sub == "scope":
        if not ref or not session_id:
            return "Usage: /mandate scope <job>  (binds this chat session to a job's mandate)"
        job = store.job(ref) or store.watch(ref)
        store.set_scope(str(session_id), job["job_id"])
        return f"This session now runs under the mandate for {job['job_name'] or job['job_id']}."
    if sub == "unscope":
        if session_id:
            store.set_scope(str(session_id), None)
        return "This session is no longer bound to a mandate."
    job = _job_for(eng, ref, session_id)
    if job is None:
        names = [j["job_name"] or j["job_id"] for j in store.jobs()]
        return "No job selected. Known jobs: " + (", ".join(names) if names else "none yet") + \
            ". Use /mandate status <job> or /mandate scope <job>."
    if sub == "drift":
        return drift_text(eng, job, color=False)
    return status_text(eng, job, color=False)

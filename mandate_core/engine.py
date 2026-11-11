"""Hook logic: observe and enforce. Hermes-agnostic so it can be unit-tested with fakes."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

from . import policy as P
from .normalize import CallShape, normalize_call
from .store import Store

log = logging.getLogger("mandate")


@dataclass
class Scope:
    job_id: str
    job_name: str | None
    run_id: str
    unattended: bool


class Engine:
    def __init__(self, store_factory: Callable[[], Store], config: Callable[[str, Any], Any],
                 cron: Callable[[], Any], cwd: Callable[[], str]):
        self._store_factory = store_factory
        self._store: Store | None = None
        self._lock = threading.Lock()
        self.config = config
        self.cron = cron
        self.cwd = cwd
        self._policy_cache: dict[str, P.Policy] = {}
        self.hooks_registered = False

    @property
    def store(self) -> Store:
        if self._store is None:
            with self._lock:
                if self._store is None:
                    self._store = self._store_factory()
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    # ---------------------------------------------------------------- scope & mode

    def scope(self, kwargs: dict) -> Scope | None:
        ex = self.cron()
        if ex is not None and getattr(ex, "job_id", None):
            return Scope(str(ex.job_id), getattr(ex, "job_name", None) or None,
                         str(getattr(ex, "execution_id", "") or "cron"), True)
        session_id = kwargs.get("session_id") or ""
        if session_id:
            job_id = self.store.scope_for(str(session_id))
            if job_id:
                return Scope(job_id, None, str(session_id), False)
        return None

    def default_mode(self) -> str:
        mode = str(self.config("mode", "observe") or "observe")
        return mode if mode in ("observe", "enforce") else "enforce"

    def parsed(self, row) -> P.Policy:
        sha = row["sha256"]
        pol = self._policy_cache.get(sha)
        if pol is None:
            pol = P.parse(row["yaml"])
            if P.content_hash(row["yaml"]) != sha:
                raise ValueError("stored mandate does not match its approval hash")
            self._policy_cache[sha] = pol
        return pol

    @staticmethod
    def shape_for(tool_name: str, args: Any, cwd: str, root: str) -> tuple[CallShape, dict]:
        """Normalize a call and build the redacted record stored as an observation (live and learned)."""
        shape = normalize_call(tool_name, args if args is not None else {}, cwd, root)
        data = shape.to_json()
        data["root"] = root
        return shape, data

    # ---------------------------------------------------------------- hooks

    def pre_tool_call(self, tool_name: str = "", args: Any = None, **kwargs: Any) -> dict | None:
        mode: str | None = None
        scope: Scope | None = None
        try:
            scope = self.scope(kwargs)
            if scope is None:
                return None
            store = self.store
            job = store.bind_job(scope.job_id, scope.job_name)
            if scope.unattended and not job["watched"] and not self.config("observe_all_cron", True):
                return None
            mode = job["mode"] or self.default_mode()
            row = store.active_policy(scope.job_id)
            pol = self.parsed(row) if row is not None else None
            cwd = self.cwd() or os.getcwd()
            root = (pol.workdir if pol and pol.workdir else cwd)
            shape, data = self.shape_for(tool_name, args, cwd, root)
            if shape.exempt:
                return None
            run = store.touch_run(scope.job_id, scope.run_id, mode)
            tcid = kwargs.get("tool_call_id")
            violations = P.check(shape, pol, run["writes"]) if pol is not None else []
            if mode == "enforce" and pol is not None and violations:
                return self._violation(scope, job, row, shape, data, violations, tcid)
            if mode == "enforce" and pol is None:
                # Enforce with nothing approved: nothing to enforce against yet, keep observing.
                outcome = "observed"
            else:
                outcome = ("allowed" if mode == "enforce" else ("would_block" if violations else "observed"))
            store.record(scope.job_id, scope.run_id, data, outcome, tcid, shape.write_count)
            return None
        except Exception as exc:  # fail closed unless we positively know we are only observing
            log.exception("mandate pre_tool_call failed")
            if scope is None and mode is None:
                try:
                    in_cron = self.cron() is not None
                except Exception:
                    in_cron = True
                if not in_cron:
                    return None
            if mode == "observe":
                return None
            return {"action": "block", "message": (
                f"MANDATE BLOCKED {tool_name}: Mandate could not evaluate this call ({type(exc).__name__}) "
                "and fails closed. Run `hermes mandate doctor`.")}

    def _violation(self, scope: Scope, job, row, shape: CallShape, data: dict, violations: list[P.Violation],
                   tcid: str | None) -> dict:
        store = self.store
        first = violations[0]
        interactive_action = str(self.config("interactive_violation", "approve") or "approve")
        escalate = (not scope.unattended) and interactive_action == "approve"
        drift_id = store.add_drift(scope.job_id, scope.run_id, shape.tool, shape.summary, first.family,
                                   first.subject, "; ".join(v.reason for v in violations)[:1000], row["version"],
                                   "escalated" if escalate else "blocked", data)
        store.record(scope.job_id, scope.run_id, data, "escalated" if escalate else "blocked", tcid, 0)
        name = job["job_name"] or scope.job_id
        msg = format_block(name, scope.run_id, row["version"], row["sha256"], shape.summary, violations, drift_id,
                           scope.unattended and True)
        if escalate:
            key = hashlib.sha256(f"{scope.job_id}:{first.family}:{first.subject}".encode()).hexdigest()[:16]
            return {"action": "approve", "message": msg, "rule_key": f"mandate:{key}"}
        return {"action": "block", "message": msg}

    def post_tool_call(self, tool_name: str = "", args: Any = None, **kwargs: Any) -> None:
        try:
            tcid = kwargs.get("tool_call_id")
            if tcid and self.scope(kwargs) is not None:
                self.store.mark_status(str(tcid), str(kwargs.get("status") or "ok"))
        except Exception:
            log.debug("mandate post_tool_call failed", exc_info=True)
        return None


def format_block(job: str, run_id: str, version: int, sha: str, summary: str, violations: list[P.Violation],
                 drift_id: int, unattended: bool) -> str:
    lines = [f"MANDATE BLOCKED this call. Job {job}, run {run_id[:12]}, mandate v{version} sha256:{sha[:12]}",
             f"  call: {summary}"]
    for v in violations[:4]:
        lines.append(f"  failed rule [{v.family}]: {v.reason}")
    lines.append(f"  drift: #{drift_id}")
    lines.append(f"  to allow it: hermes mandate widen {job} {drift_id}   then: hermes mandate approve {job}")
    if unattended:
        lines.append("This is an unattended cron run, so the call was refused, not escalated. "
                     "Finish the parts of the task the mandate allows; mandate_status shows the rules.")
    return "\n".join(lines)

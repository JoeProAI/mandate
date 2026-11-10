"""Hermes entry point: registers hooks, the status tool, CLI and slash command."""

from __future__ import annotations

import json
import os
from typing import Any

from .engine import Engine
from .store import Store

STATUS_SCHEMA = {
    "name": "mandate_status",
    "description": ("Read-only. Show the active Mandate (least-privilege policy) for the current job and the "
                    "calls it blocked in this run, so you can finish the parts of the task it allows."),
    "parameters": {"type": "object", "properties": {
        "job": {"type": "string", "description": "Job id or name. Defaults to the current job."}},
        "required": []},
}

_ENGINE: Engine | None = None


def engine() -> Engine | None:
    return _ENGINE


def build_engine(ctx: Any) -> Engine:
    def config(key: str, default: Any) -> Any:
        try:
            return ctx.get_config(key, default)
        except Exception:
            return default

    def cron() -> Any:
        return ctx.current_cron_execution()

    def cwd() -> str:
        try:
            return ctx.session_cwd() or os.getcwd()
        except Exception:
            return os.getcwd()

    return Engine(lambda: Store(), config, cron, cwd)


def register(ctx: Any) -> None:
    global _ENGINE
    eng = build_engine(ctx)
    _ENGINE = eng
    from . import cli, tool

    ctx.register_hook("pre_tool_call", eng.pre_tool_call)
    ctx.register_hook("post_tool_call", eng.post_tool_call)
    ctx.register_tool(name="mandate_status", toolset="mandate", schema=STATUS_SCHEMA,
                      handler=lambda args=None, **kw: tool.status(eng, args or {}, **kw),
                      description=STATUS_SCHEMA["description"], emoji="")
    ctx.register_cli_command("mandate", "Learned least-privilege for cron jobs", cli.setup,
                             lambda a: cli.dispatch(a, eng),
                             description="Observe a job, propose a mandate, approve it, enforce it.")
    ctx.register_command("mandate", lambda raw, **kw: tool.slash(eng, raw, **kw),
                         description="Mandate status and drift for this session or a job",
                         args_hint="[status|drift|scope <job>|unscope] [job]")
    eng.hooks_registered = True

    def _unload() -> None:
        global _ENGINE
        eng.hooks_registered = False
        eng.close()
        if _ENGINE is eng:
            _ENGINE = None

    ctx.on_unload(_unload)


def status_json(eng: Engine, job_ref: str) -> str:
    from .tool import status

    return status(eng, {"job": job_ref})


__all__ = ["register", "engine", "json"]

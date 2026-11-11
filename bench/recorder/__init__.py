"""Bench-only recorder: appends one JSON line per tool call to $BENCH_TRACE. Never blocks."""
import json
import os
import time

_T0: dict = {}


def _cron(ctx):
    try:
        c = ctx.current_cron_execution()
        return (getattr(c, "job_name", None), getattr(c, "execution_id", None)) if c else (None, None)
    except Exception:
        return (None, None)


def register(ctx):
    path = os.environ.get("BENCH_TRACE")

    def write(rec):
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def pre(tool_name=None, args=None, tool_call_id=None, **kw):
        _T0[tool_call_id] = time.perf_counter()
        job, ex = _cron(ctx)
        write({"ev": "pre", "t": time.time(), "job": job, "exec": ex, "tool": tool_name, "args": args,
               "call_id": tool_call_id})
        return None

    def post(tool_name=None, args=None, result=None, tool_call_id=None, status=None, error_type=None,
             duration_ms=None, **kw):
        job, ex = _cron(ctx)
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        write({"ev": "post", "t": time.time(), "job": job, "exec": ex, "tool": tool_name, "call_id": tool_call_id,
               "status": status, "error_type": error_type, "duration_ms": duration_ms,
               "result_head": (text or "")[:1500]})

    ctx.register_hook("pre_tool_call", pre)
    ctx.register_hook("post_tool_call", post)

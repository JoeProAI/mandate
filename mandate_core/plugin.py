"""Hermes entry point: registers hooks, the status tool, CLI and slash command."""

from __future__ import annotations

import json
from typing import Any

STATUS_SCHEMA = {
    "name": "mandate_status",
    "description": "Read-only. Show the active Mandate policy for the current job and why recent calls were blocked.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def _pre_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> None:
    return None


def _post_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> None:
    return None


def _status_tool(args: dict | None = None, **kwargs: Any) -> str:
    return json.dumps({"mandate": None, "mode": "observe"})


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_tool(name="mandate_status", toolset="mandate", schema=STATUS_SCHEMA,
                      handler=_status_tool, description=STATUS_SCHEMA["description"])

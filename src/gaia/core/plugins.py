"""ADK runtime plugins for Gaia.

:class:`ToolLoggingPlugin` is the **single** place tool calls are logged. ADK fires its
``after_tool_callback`` / ``on_tool_error_callback`` for *every* tool — our function
tools, ADK built-ins (``load_memory``), and MCP toolset tools alike — so tools never
hand-roll their own logging. The plugin emits one ``tool_used`` event per call: the base
fields (tool, agent, status) plus the call's **arguments**, sanitized the way web
frameworks filter request params (Rails ``filter_parameters`` / Sentry scrubbers):

1. values of keys whose *name* looks sensitive (``token``, ``password``, ``api_key``,
   ``auth``…) are replaced with ``[filtered]``,
2. the few args whose key name *can't* signal sensitivity are filtered via the tiny
   per-tool :data:`_DROP` map (``browser_type.text`` may be a typed password,
   ``remember.fact`` is private by definition),
3. every value is truncated, and the global log redaction (:mod:`gaia.logs`) scrubs
   known live secrets as a final net.

Tool **results** are never logged — only their ``status`` — they are the largest and
least predictable secret surface (page contents, file bodies, recalled memories).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from google.adk.plugins.base_plugin import BasePlugin

from gaia.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

#: Arg keys whose value is replaced with ``[filtered]`` regardless of tool. Substrings
#: follow Rails' filter_parameters defaults (``passw`` not ``pass`` to spare "compass";
#: ``key``/``auth`` need word boundaries to spare "keywords"/"author").
_SENSITIVE_KEY = re.compile(
    r"passw|secret|token|credential|authorization|bearer|salt|(?:^|[_-])(?:key|auth)(?:[_-]|$)",
    re.IGNORECASE,
)

#: Per-tool args to filter when the key name alone can't signal sensitivity.
_DROP: dict[str, frozenset[str]] = {
    "browser_type": frozenset({"text"}),  # the typed text may be a password
    "remember": frozenset({"fact"}),  # the fact is private by definition
}

_FILTERED = "[filtered]"
_MAX_VALUE_CHARS = 150


def _sanitize(tool_name: str, args: Any) -> dict[str, Any]:
    """Filter + truncate a tool call's arguments for logging. Never raises."""
    if not isinstance(args, dict):
        return {}
    drop = _DROP.get(tool_name, frozenset())
    fields: dict[str, Any] = {}
    for key, value in args.items():
        key_str = str(key)
        if key_str in drop or _SENSITIVE_KEY.search(key_str):
            fields[key_str] = _FILTERED
        elif isinstance(value, bool | int | float) or value is None:
            fields[key_str] = value
        else:
            text = str(value)
            if len(text) > _MAX_VALUE_CHARS:
                text = text[:_MAX_VALUE_CHARS] + "…"
            fields[key_str] = text
    return fields


def _base_fields(name: str, tool_context: ToolContext | None) -> dict[str, Any]:
    """The fields logged for every tool: its id and the calling agent (when known)."""
    fields: dict[str, Any] = {"tool": name}
    agent = getattr(tool_context, "agent_name", None)
    if agent:
        fields["agent"] = agent
    return fields


class ToolLoggingPlugin(BasePlugin):
    """Emit exactly one ``tool_used`` event per tool call, for every tool."""

    def __init__(self) -> None:
        super().__init__(name="tool_logging")

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> None:
        name = getattr(tool, "name", type(tool).__name__)
        fields = _base_fields(name, tool_context)
        fields["status"] = result.get("status", "ok") if isinstance(result, dict) else "ok"
        try:
            args = _sanitize(name, tool_args)
        except Exception:  # pragma: no cover - defensive; never break a tool over logging
            args = {}
        if args:
            fields["args"] = args
        log_event("tool_used", **fields)
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> None:
        name = getattr(tool, "name", type(tool).__name__)
        fields = _base_fields(name, tool_context)
        fields["status"] = "error"
        fields["error"] = type(error).__name__
        try:
            args = _sanitize(name, tool_args)
        except Exception:  # pragma: no cover - defensive; never break a tool over logging
            args = {}
        if args:
            fields["args"] = args
        log_event("tool_used", **fields)
        return None

"""ADK runtime plugins for God.

:class:`ToolLoggingPlugin` is the **single** place tool calls are logged. ADK fires its
``after_tool_callback`` / ``on_tool_error_callback`` for *every* tool — our function
tools, ADK built-ins (``load_memory``), and MCP toolset tools alike — so tools never
hand-roll their own logging. The plugin emits one ``tool_used`` event per call with a
small set of base fields (tool, agent, status) plus a few rich, **secret-safe**
fields drawn from a central per-tool policy (:data:`_FIELD_POLICY`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.adk.plugins.base_plugin import BasePlugin

from godpy.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

# Per-tool extra log fields, keyed by tool id. Each entry maps (tool_args, result) to a
# dict of fields to add beyond the base tool/agent/status. This table is the ONE place
# secret-bearing args are kept out of the logs — note what is deliberately absent:
# browser_type drops ``text`` (passwords), exec truncates ``command``, remember logs
# nothing about the fact itself. Tools with no entry log base fields only.
_FIELD_POLICY: dict[str, Any] = {
    "web_search": lambda a, r: {
        "query": str(a.get("query", "")).strip(),
        "results": len(r.get("results", [])),
    },
    "web_fetch": lambda a, r: {
        "url": str(a.get("url", "")).strip(),
        "chars": len(str(r.get("markdown", ""))),
    },
    "fs_read": lambda a, r: {"path": a.get("path")},
    "fs_write": lambda a, r: {"path": a.get("path")},
    "fs_edit": lambda a, r: {"path": a.get("path")},
    "fs_glob": lambda a, r: {"pattern": a.get("pattern")},
    "fs_grep": lambda a, r: {"pattern": a.get("pattern")},
    "browser_navigate": lambda a, r: {"url": a.get("url")},
    "browser_click": lambda a, r: {"ref": a.get("ref")},
    # NEVER log a.get("text") — it may be a password.
    "browser_type": lambda a, r: {"ref": a.get("ref"), "submit": a.get("submit", False)},
    "browser_screenshot": lambda a, r: {"path": r.get("path")},
    # exec: truncate the command (may carry secrets), never log it whole.
    "exec": lambda a, r: {
        "command": str(a.get("command", ""))[:120],
        "background": a.get("background", False),
        "exit_code": r.get("exit_code"),
    },
    "exec_poll": lambda a, r: {"process": a.get("process_id")},
    "exec_kill": lambda a, r: {"process": a.get("process_id")},
    "exec_list": lambda a, r: {"count": len(r.get("processes", []))},
    # remember: deliberately log nothing about the fact (it may be sensitive).
    "remember": lambda a, r: {},
    "delegate_to_soul": lambda a, r: {"soul": r.get("soul"), "forged": r.get("created")},
}


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
        # The per-tool policy adds rich, secret-safe fields. A misbehaving policy (e.g.
        # unexpected args) must never break logging — fall back to the base fields.
        policy = _FIELD_POLICY.get(name)
        if policy is not None:
            try:
                fields.update(policy(tool_args or {}, result if isinstance(result, dict) else {}))
            except Exception:  # pragma: no cover - defensive; never break a tool over logging
                pass
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
        log_event("tool_used", **fields)
        return None

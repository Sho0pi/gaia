"""ADK runtime plugins for God.

:class:`ToolLoggingPlugin` makes every tool call visible. godpy's own tools already
emit a rich ``tool_used`` event from their ``done()`` closure, but ADK built-ins (e.g.
``load_memory``) log nothing — so without this, a memory-heavy session shows no tool
activity at all. The plugin logs a ``tool_used`` event for any tool that doesn't
self-log, filling the gap without double-logging the ones that do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.adk.plugins.base_plugin import BasePlugin

from godpy.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Collection

    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext


class ToolLoggingPlugin(BasePlugin):
    """Emit a ``tool_used`` event for every tool call not covered by a tool's own log."""

    def __init__(self, self_logging: Collection[str]) -> None:
        super().__init__(name="tool_logging")
        self._skip = frozenset(self_logging)

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> None:
        name = getattr(tool, "name", type(tool).__name__)
        if name in self._skip:
            return None
        status = result.get("status", "ok") if isinstance(result, dict) else "ok"
        log_event("tool_used", tool=name, status=status)
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
        if name in self._skip:
            return None
        log_event("tool_used", tool=name, status="error", error=type(error).__name__)
        return None

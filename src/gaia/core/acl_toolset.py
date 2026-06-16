"""A dynamic ADK toolset that resolves the caller's allowed tools live, per turn.

ADK calls a toolset's ``get_tools(readonly_context)`` on *every* request (the result is
cached only within one invocation id), and the context carries ``user_id``. So instead of
baking an ACL-filtered tool list into the agent at build time — which goes stale the moment
a capability is granted, forcing a Runner rebuild that drops the conversation — we attach
this toolset once and let it re-read the user's current capabilities each turn.

Result: ``/grant`` / ``/revoke`` take effect on the user's *next message* with no session
reset (the model keeps the conversation). The hard gate
(:class:`gaia.core.plugins.ToolPermissionPlugin`) stays as defense in depth and covers
souls, which use a static tool list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents.readonly_context import ReadonlyContext

    from gaia.core.agent import Gaia


class AclToolset(BaseToolset):
    """Registry tools, filtered to the current user's capabilities on each invocation."""

    def __init__(self, gaia: Gaia) -> None:
        super().__init__()
        self._gaia = gaia

    async def get_tools(self, readonly_context: ReadonlyContext | None = None) -> list[BaseTool]:
        from gaia.acl import allowed_tool_ids

        registry = self._gaia.tools
        registry_ids = set(registry.names())
        user_id = getattr(readonly_context, "user_id", None)
        # No resolved user (cron / single-user / dev web) is trusted with every tool —
        # allowed_tool_ids returns all for a None user, matching the handler's admin default.
        # Only registry tools live here; off-registry MCP tools attach separately and are
        # gated by ToolPermissionPlugin (which applies the same group/prefix rules).
        user = self._gaia.users.get(user_id) if user_id else None
        allowed = allowed_tool_ids(user, self._gaia.config, registry_ids)

        tools: list[BaseTool] = []
        for name in registry.names():
            if name not in allowed:
                continue
            tool = registry.get(name)
            # Registry entries are plain callables (ADK wraps them in FunctionTool) or
            # already-built BaseTools (e.g. load_memory_tool). A BaseToolset can't be ACL'd
            # per-tool here and none are registered, so skip that (typed) case defensively.
            if isinstance(tool, BaseTool):
                tools.append(tool)
            elif callable(tool):
                tools.append(FunctionTool(func=tool))
        return tools

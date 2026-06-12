"""The ``exec_kill`` tool: stop a background process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.shell.base import ProcessManager, err

NAME = "exec_kill"


def make_exec_kill(manager: ProcessManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``exec_kill`` tool bound to ``manager``."""

    async def exec_kill(process_id: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Stop a background process you started with exec(..., background=True).

        Args:
            process_id: the id returned by the background exec.
        """
        agent = tool_context.agent_name

        managed = manager.get(agent, process_id.strip())
        if managed is None:
            return err(f"unknown process {process_id!r} (it may belong to another agent)")

        try:
            exit_code = await manager.kill(managed)
        except Exception as exc:
            return err(f"failed to stop process: {exc}")
        return {"status": "success", "process_id": managed.process_id, "exit_code": exit_code}

    return exec_kill

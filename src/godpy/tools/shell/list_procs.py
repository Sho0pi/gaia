"""The ``exec_list`` tool: list the agent's background processes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy.tools.shell.base import ProcessManager

NAME = "exec_list"


def make_exec_list(manager: ProcessManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``exec_list`` tool bound to ``manager``."""

    async def exec_list(*, tool_context: ToolContext) -> dict[str, Any]:
        """List your background processes and whether each is still running.

        Returns:
            dict: {'status': 'success', 'processes': [{'process_id': str, 'command':
            str, 'status': 'running'|'exited', 'exit_code': int|None}, ...]}.
        """
        agent = tool_context.agent_name
        processes = [
            {
                "process_id": p.process_id,
                "command": p.command,
                "status": "running" if p.running else "exited",
                "exit_code": p.exit_code,
            }
            for p in manager.list(agent)
        ]
        return {"status": "success", "processes": processes}

    return exec_list

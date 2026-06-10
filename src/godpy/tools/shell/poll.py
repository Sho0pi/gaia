"""The ``exec_poll`` tool: read new output (and status) from a background process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy.tools.shell.base import ProcessManager, err

NAME = "exec_poll"


def make_exec_poll(manager: ProcessManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``exec_poll`` tool bound to ``manager``."""

    async def exec_poll(process_id: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Check a background process and read its output since you last polled.

        Use the process_id from a background exec. Output is incremental: each call
        returns only what's new. When the process has finished, 'status' is 'exited'
        and 'exit_code' is set.

        Args:
            process_id (str): The id returned by exec(..., background=True).

        Returns:
            dict: On success {'status': 'running'|'exited', 'exit_code': int|None,
            'output': str, 'truncated': bool, 'log': str}. On failure {'status':
            'error', 'error_message': str}.
        """
        agent = tool_context.agent_name

        managed = manager.get(agent, process_id.strip())
        if managed is None:
            return err(f"unknown process {process_id!r} (it may belong to another agent)")

        output, truncated = managed.consume_new_output()
        return {
            "status": "running" if managed.running else "exited",
            "exit_code": managed.exit_code,
            "output": output,
            "truncated": truncated,
            "log": str(managed.log_path),
        }

    return exec_poll

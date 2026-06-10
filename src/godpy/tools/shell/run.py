"""The ``exec`` tool: run a shell command in the agent's workspace (foreground or background)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy import constants
from godpy.tools.fs.base import SandboxError, sandbox_for
from godpy.tools.shell.base import (
    ProcessManager,
    Spawner,
    check_command,
    err,
    run_foreground,
    truncate,
)

NAME = "exec"

#: Foreground timeout bounds (seconds). Background processes have no timeout.
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 300.0


def make_exec(
    manager: ProcessManager,
    spawner: Spawner,
    *,
    security: str = "allowlist",
    allowlist: tuple[str, ...] = (),
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``exec`` tool bound to ``manager``/``spawner`` and the safety policy."""

    async def exec(
        command: str,
        timeout_seconds: float = 30.0,
        workdir: str = "",
        background: bool = False,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Run a shell command in your workspace and return its output.

        Use this to install deps, run a build, run tests, or invoke a CLI. The command
        runs in your workspace directory. Set background=True for a long-running process
        (a dev server, a long build): it returns a process_id immediately, then use
        exec_poll to read its output, exec_kill to stop it, and exec_list to see them.

        Args:
            command (str): The shell command to run.
            timeout_seconds (float): Foreground only — max seconds to wait (1-300,
                default 30); the command is killed if it exceeds this.
            workdir (str): Optional subdirectory to run in (must stay in your workspace);
                empty means the workspace root.
            background (bool): Run as a long-lived background process instead of waiting.

        Returns:
            dict: Foreground success {'status': 'success', 'exit_code': int, 'stdout':
            str, 'truncated': bool}; background {'status': 'running', 'process_id': str,
            'command': str, 'log': str}. On failure {'status': 'error', 'error_message': str}.
        """
        agent = tool_context.agent_name

        policy_error = check_command(command, security=security, allowlist=allowlist)
        if policy_error is not None:
            return err(policy_error)

        sandbox = sandbox_for(constants.AGENTS_DIR, agent)
        try:
            cwd = sandbox.resolve(workdir) if workdir else sandbox.primary
        except SandboxError as exc:
            return err(str(exc))

        if background:
            try:
                managed = await manager.spawn(agent, command, cwd)
            except Exception as exc:
                return err(f"failed to start process: {exc}")
            return {
                "status": "running",
                "process_id": managed.process_id,
                "command": command,
                "log": str(managed.log_path),
            }

        timeout = max(MIN_TIMEOUT, min(timeout_seconds, MAX_TIMEOUT))
        try:
            output, exit_code, timed_out = await run_foreground(spawner, command, cwd, timeout)
        except Exception as exc:
            return err(f"exec failed: {exc}")
        if timed_out:
            return err(f"command timed out after {timeout:.0f}s and was killed")

        stdout, was_truncated = truncate(output)
        status = "success" if exit_code == 0 else "error"
        result: dict[str, Any] = {
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout,
            "truncated": was_truncated,
        }
        if status == "error":
            result["error_message"] = f"command exited with code {exit_code}"
        return result

    return exec

"""The ``exec`` tool: run a shell command in the agent's workspace (foreground or background)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools.fs.base import SandboxError, sandbox_for
from gaia.tools.shell.base import (
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
        port: int = 0,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Run a shell command in your workspace (install deps, build, test, CLIs).

        Set background=True for a long-running process (dev server, long build): returns a
        process_id; then exec_poll reads output, exec_kill stops it, exec_list lists them.
        For a background dev server, pass the port it binds (e.g. 'vite --port 5173' +
        port=5173) so you can browser_navigate to it.

        Args:
            command: the shell command to run.
            timeout_seconds: foreground only — max seconds to wait (1-300).
            workdir: subdirectory to run in (must stay inside your workspace).
            background: run a long-lived background process instead of waiting.
            port: loopback port a background server binds (0 = none/auto-detect).
        """
        command, workdir = command or "", workdir or ""  # a model may send null, not the default
        agent = tool_context.agent_name

        policy_error = check_command(command, security=security, allowlist=allowlist)
        if policy_error is not None:
            return err(policy_error)

        sandbox = sandbox_for(constants.AGENTS_DIR, agent)
        try:
            # exec can mutate, so its cwd must be writable — the root can't cd into a soul's
            # workspace to change it (re-delegate instead); no workdir stays in its own primary.
            cwd = sandbox.resolve(workdir, write=True) if workdir else sandbox.primary
        except SandboxError as exc:
            return err(str(exc))

        if background:
            try:
                managed = await manager.spawn(agent, command, cwd, port=port)
            except Exception as exc:
                return err(f"failed to start process: {exc}")
            started: dict[str, Any] = {
                "status": "running",
                "process_id": managed.process_id,
                "command": command,
                "log": str(managed.log_path),
            }
            if port:
                started["url"] = f"http://127.0.0.1:{port}/"
            return started

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

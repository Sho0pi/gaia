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
    needs_confirmation,
    run_foreground,
    truncate,
)

NAME = "exec"

#: Foreground timeout bounds (seconds). Background processes have no timeout.
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 300.0


def _confirmation_gate(tool_context: ToolContext, command: str) -> dict[str, Any] | None:
    """Gate a risky command on the human's approval (ADK native tool confirmation).

    Returns ``None`` when it's approved (run it). Returns a result dict to stop: the first call
    requests confirmation and ADK pauses the run (``awaiting_approval``); on resume the user's
    decision is in ``tool_context.tool_confirmation``. With no human reachable this turn the command
    is denied rather than left hanging.
    """
    from gaia.core.elicit import interactive_turn

    conf = tool_context.tool_confirmation
    if conf is not None:  # resumed with the user's decision
        return None if conf.confirmed else err("command was not approved by the user")
    if not interactive_turn.get():
        return err("command needs approval but no one is available to approve it")
    tool_context.request_confirmation(hint=f"Run this command? {command!r}")
    return {"status": "awaiting_approval", "command": command}


def make_exec(
    manager: ProcessManager,
    spawner: Spawner,
    *,
    security: str = "ask",
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

        A risky or unfamiliar command may need the user's approval first — you'll get back
        ``status: "awaiting_approval"`` and the run pauses until they answer; you don't need to do
        anything, it resumes automatically. Common, safe commands run without asking.

        Set background=True for a long-running process (dev server, long build): returns a
        process_id for exec_poll/exec_kill/exec_list. For a background dev server, pass its
        port (e.g. 'vite --port 5173' + port=5173) so you can browser_navigate to it.

        Args:
            timeout_seconds: foreground only — max seconds to wait (1-300).
            workdir: a subdirectory inside your workspace.
            port: loopback port a background server binds (0 = none/auto-detect).
        """
        agent = tool_context.agent_name

        policy_error = check_command(command, security=security, allowlist=allowlist)
        if policy_error is not None:
            return err(policy_error)

        # 'ask' mode: a risky/unknown command pauses for the human's yes/no via ADK's native tool
        # confirmation (the model can't bypass it). A safe (allowlisted) command runs unprompted.
        if security == "ask" and needs_confirmation(command, allowlist=allowlist):
            denied = _confirmation_gate(tool_context, command)
            if denied is not None:
                return denied

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

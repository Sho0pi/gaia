"""Shared foundation for the ``exec`` tools: command safety + the process manager.

``exec`` runs a shell command in the calling agent's workspace. Two shapes:

* **foreground** — run, capture, wait (with a timeout); the common case.
* **background** — spawn a long-lived process (dev server, build), return a
  ``process_id``, and manage it later with ``exec_poll`` / ``exec_kill`` / ``exec_list``.

Background processes are *stateful*, so they live in a per-agent
:class:`ProcessManager` keyed on ``tool_context.agent_name`` (one soul's processes
never bleed into another's), exactly like the browser session manager. The manager
terminates everything on process exit (``atexit``) so nothing orphans.

Both shapes go through a :class:`Spawner` (default: ``asyncio.create_subprocess_shell``)
— the seam a future Docker backend slots into. Output is streamed into a capped
in-memory buffer (for incremental polling) *and* teed to a per-process log file in the
workspace, so the full transcript persists and the agent can ``fs_read`` it.
"""

from __future__ import annotations

import asyncio
import atexit
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Per-poll and per-foreground output cap (chars) before truncation kicks in.
OUTPUT_CHAR_CAP = 50_000

#: How much of a background process's output is kept in memory (the full log is teed
#: to disk regardless); older bytes are dropped from the buffer once this is exceeded.
BUFFER_CHAR_CAP = 1_000_000

#: Subdir of the agent workspace where background process logs are written.
LOG_DIR_NAME = ".gaia-logs"

#: Grace period (seconds) between terminate() and a hard kill().
KILL_GRACE = 5.0

#: A spawner opens a process for ``command`` in ``cwd`` and returns it. The seam a
#: Docker/remote backend replaces; the default runs it locally via asyncio.
Spawner = Callable[[str, Path, "dict[str, str] | None"], Awaitable["asyncio.subprocess.Process"]]


# --- command safety ---------------------------------------------------------------

#: Destructive commands refused in *every* security mode (including ``off``). Each is a
#: substring/pattern match on the raw command — defence in depth, not a parser.
_DENYLIST: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME)(\s|/|$)"), "recursive rm of / or home"),
    (re.compile(r"\bmkfs\b"), "filesystem format (mkfs)"),
    (re.compile(r"\bdd\b[^|]*\bof=/dev/"), "dd onto a device"),
    (re.compile(r">\s*/dev/(sd|nvme|disk|hd)"), "redirect onto a raw disk device"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "power state change"),
    (re.compile(r"\b(curl|wget)\b.*\|\s*(sudo\s+)?(sh|bash|zsh)\b"), "pipe-to-shell install"),
)

#: Command chaining/substitution rejected in allowlist mode (one call = one command).
_CHAIN_RE = re.compile(r"(;|&&|\|\||\||`|\$\()")

#: Sensible default allowlist (read-ish dev tooling). Overridable via gaia.yaml.
#: bun/bunx is the repo's standard JS runtime (same as the browser/MCP backends).
DEFAULT_ALLOWLIST = (
    "ls", "cat", "echo", "pwd", "git", "python", "python3", "node", "bun", "bunx",
    "pip", "pip3", "uv", "pytest", "grep", "find", "head", "tail", "wc", "make",
)  # fmt: skip


def check_command(command: str, *, security: str, allowlist: tuple[str, ...]) -> str | None:
    """Return an error string if ``command`` is refused under ``security``, else None.

    The denylist applies in every mode. ``allowlist`` mode additionally requires the
    first token to be permitted and forbids chaining. ``ask`` mode is not wired yet
    (needs the human-in-the-loop ask tool, issue #29).
    """
    stripped = command.strip()
    if not stripped:
        return "command must not be empty"
    for pattern, why in _DENYLIST:
        if pattern.search(stripped):
            return f"refused: matches the exec denylist ({why})"

    if security == "off":
        return None
    if security == "ask":
        return "exec 'ask' security mode needs the human-in-the-loop ask tool (see issue #29)"
    if security == "allowlist":
        if _CHAIN_RE.search(stripped):
            return "refused: command chaining is not allowed in allowlist mode (run one command)"
        binary = stripped.split()[0]
        if binary not in allowlist:
            return f"refused: {binary!r} is not in the exec allowlist (tools.exec.allowlist)"
        return None
    return f"unknown exec security mode {security!r} (use off/allowlist/ask)"


def truncate(text: str, cap: int = OUTPUT_CHAR_CAP) -> tuple[str, bool]:
    """Return ``text`` capped to its last ``cap`` chars and whether it was cut."""
    if len(text) <= cap:
        return text, False
    return text[-cap:], True


def err(message: str) -> dict[str, Any]:
    """A standard error result dict (matches the fs/browser tools)."""
    return {"status": "error", "error_message": message}


# --- process spawning + management ------------------------------------------------


async def local_spawner(
    command: str, cwd: Path, env: dict[str, str] | None
) -> asyncio.subprocess.Process:
    """Default spawner: run ``command`` locally via the shell, stderr merged into stdout."""
    return await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


def _open_log(path: Path) -> Any:
    """Open ``path`` for binary append, creating its parent. Sync (not in a coroutine)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("ab")


@dataclass
class ManagedProcess:
    """A spawned background process plus its captured output and log file."""

    process_id: str
    agent: str
    command: str
    proc: asyncio.subprocess.Process
    log_path: Path
    _buf: str = ""
    _total: int = 0  # absolute chars ever produced
    _delivered: int = 0  # absolute chars already returned by poll
    exit_code: int | None = None
    started: float = field(default_factory=time.monotonic)
    # The background task draining the process's output into _buf + the log file. Held
    # so the manager can await it after terminating the process (so the final output
    # and exit_code are captured before we report back). Set by ProcessManager.spawn.
    _pump_task: asyncio.Task[None] | None = None

    def _append(self, chunk: str) -> None:
        self._total += len(chunk)
        self._buf += chunk
        if len(self._buf) > BUFFER_CHAR_CAP:
            self._buf = self._buf[-BUFFER_CHAR_CAP:]

    async def pump(self) -> None:
        """Drain the merged output stream into the buffer + log until the process exits."""
        log = _open_log(self.log_path)
        try:
            assert self.proc.stdout is not None
            while True:
                chunk = await self.proc.stdout.read(4096)
                if not chunk:
                    break
                log.write(chunk)
                log.flush()
                self._append(chunk.decode("utf-8", errors="replace"))
            self.exit_code = await self.proc.wait()
        finally:
            log.close()

    def consume_new_output(self) -> tuple[str, bool]:
        """Return the output produced since the last call, and mark it as consumed.

        Each poll gets only what's new: the cursor (``_delivered``) advances past what's
        returned. ``truncated`` is True when the in-memory buffer had already dropped
        bytes the caller never saw (a very chatty process), or the new slice exceeds the
        per-poll cap. The full transcript always remains in the log file.
        """
        buffer_start = self._total - len(self._buf)
        dropped = self._delivered < buffer_start
        start = max(self._delivered, buffer_start) - buffer_start
        new = self._buf[start:]
        self._delivered = self._total
        capped, cut = truncate(new)
        return capped, dropped or cut

    @property
    def running(self) -> bool:
        return self.exit_code is None


class ProcessManager:
    """Owns an agent's background processes and guarantees they're cleaned up.

    One instance is created by the tool registry and shared by the exec tools (each
    tool closure captures it) — so there is no module-level singleton. On its first
    spawn it registers a single ``atexit`` hook to terminate anything still running
    when the process exits, so a forgotten background process can't outlive gaia.
    """

    def __init__(self, spawner: Spawner | None = None) -> None:
        self._spawner = spawner or local_spawner
        self._procs: dict[str, ManagedProcess] = {}
        self._counter = 0
        self._cleanup_registered = False

    def _register_cleanup_once(self) -> None:
        """Arrange for ``close_all`` to run on process exit (idempotent, lazy)."""
        if not self._cleanup_registered:
            atexit.register(self._cleanup_at_exit)
            self._cleanup_registered = True

    def _cleanup_at_exit(self) -> None:
        """atexit hook: terminate any still-running processes. Best-effort."""
        if not self._procs:
            return
        try:
            asyncio.run(self.close_all())
        except Exception:  # pragma: no cover - shutdown best-effort
            pass

    async def spawn(
        self, agent: str, command: str, cwd: Path, *, env: dict[str, str] | None = None
    ) -> ManagedProcess:
        """Start ``command`` in the background and register it; returns the record."""
        self._register_cleanup_once()
        self._counter += 1
        process_id = f"proc-{self._counter}"
        log_path = cwd / LOG_DIR_NAME / f"{process_id}.log"
        proc = await self._spawner(command, cwd, env)
        managed = ManagedProcess(
            process_id=process_id, agent=agent, command=command, proc=proc, log_path=log_path
        )
        managed._pump_task = asyncio.create_task(managed.pump())
        self._procs[process_id] = managed
        return managed

    def get(self, agent: str, process_id: str) -> ManagedProcess | None:
        """Return ``agent``'s process by id, or None (also None if it belongs elsewhere)."""
        managed = self._procs.get(process_id)
        if managed is None or managed.agent != agent:
            return None
        return managed

    def list(self, agent: str) -> list[ManagedProcess]:
        """Every process belonging to ``agent``, oldest first."""
        return [p for p in self._procs.values() if p.agent == agent]

    async def kill(self, managed: ManagedProcess) -> int | None:
        """Terminate ``managed`` (hard-kill after a grace period); return its exit code."""
        if managed.running:
            managed.proc.terminate()
            try:
                await asyncio.wait_for(managed.proc.wait(), timeout=KILL_GRACE)
            except TimeoutError:
                managed.proc.kill()
                await managed.proc.wait()
        if managed._pump_task is not None:
            await managed._pump_task
        return managed.exit_code

    async def close_all(self) -> None:
        """Terminate every process across all agents; called on process exit."""
        for managed in list(self._procs.values()):
            try:
                await self.kill(managed)
            except Exception:  # pragma: no cover - shutdown best-effort
                pass
        self._procs.clear()


async def run_foreground(
    spawner: Spawner, command: str, cwd: Path, timeout_s: float, env: dict[str, str] | None = None
) -> tuple[str, int | None, bool]:
    """Run ``command`` to completion (or ``timeout_s``). Returns (output, exit_code, timed_out)."""
    proc = await spawner(command, cwd, env)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return "", None, True
    return stdout.decode("utf-8", errors="replace"), proc.returncode, False

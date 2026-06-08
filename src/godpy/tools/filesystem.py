"""Filesystem tools: ``fs_read``, ``fs_write``, ``fs_edit``, ``fs_glob``, ``fs_grep``.

Ported in spirit from ``Sho0pi/agenttools`` ``fs/`` — same params and safety, ADK
function-tool idiom (plain functions, dict return, never raise to the model).

All five tools share one :class:`Sandbox` that confines every path to the calling
agent's own workspace (``~/.godpy/agents/<agent>/workspace``) plus ``/tmp``. The agent is
read at call time from ADK's injected ``tool_context.agent_name``, so the tools register
once globally yet resolve a per-agent sandbox. The sandbox realpath-resolves its roots
once and rejects anything that escapes them (``..``, absolute paths, symlink escapes).

``fs_glob`` / ``fs_grep`` shell out to the ``fd`` and ``rg`` binaries (fast, battle-tested,
symlink-safe by default); they are only registered when those binaries are present.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy.logs import log_event

#: Tool ids (also the ADK tool names — must match the closure names).
READ = "fs_read"
WRITE = "fs_write"
EDIT = "fs_edit"
GLOB = "fs_glob"
GREP = "fs_grep"

#: Per-read byte cap and result caps for the search tools.
MAX_READ_BYTES = 10_000_000
GLOB_MAX = 500
GREP_MAX = 200
SUBPROCESS_TIMEOUT = 30

#: Files whose contents are never returned by ``fs_read`` (secrets), matched by exact
#: name, by suffix, or by any ``.git`` path component.
_DENY_NAMES = frozenset({".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc"})
_DENY_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore"})

#: Bytes considered "text" for the binary-content heuristic.
_TEXT_BYTES = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b"

_WRITE_MODES = frozenset({"create", "overwrite", "append"})


class SandboxError(Exception):
    """Raised when a path escapes the sandbox or is otherwise refused."""


class Sandbox:
    """A set of allowed roots; resolves a user path or refuses to leave them."""

    def __init__(self, primary: Path, extra_roots: tuple[Path, ...] = (Path("/tmp"),)) -> None:
        #: Relative paths anchor here (the agent's workspace), created if missing.
        self.primary = Path(os.path.realpath(primary))
        self.primary.mkdir(parents=True, exist_ok=True)
        roots = [self.primary]
        for root in extra_roots:
            if root.exists():
                roots.append(Path(os.path.realpath(root)))
        self.roots = tuple(roots)

    def resolve(self, rel: str) -> Path:
        """Resolve ``rel`` to an absolute path inside an allowed root, or raise.

        Relative input anchors on :attr:`primary`; absolute input is taken as-is. The
        result is realpath-resolved (following symlinks in any existing prefix), so a
        symlink — or a ``..`` segment — that points outside every root is rejected.
        """
        if "\x00" in rel:
            raise SandboxError("path contains a null byte")
        candidate = Path(rel) if os.path.isabs(rel) else self.primary / rel
        resolved = Path(os.path.realpath(candidate))
        if not any(resolved == root or resolved.is_relative_to(root) for root in self.roots):
            raise SandboxError(f"path escapes the workspace: {rel}")
        return resolved


_SANDBOX_CACHE: dict[tuple[str, str], Sandbox] = {}


def _safe_dir(agent_name: str) -> str:
    """A filesystem-safe directory name for an agent (defensive; names are pre-lowered)."""
    cleaned = re.sub(r"[^a-z0-9_-]", "_", agent_name.lower()).strip("_")
    return cleaned or "agent"


def _sandbox_for(agents_dir: Path, agent_name: str) -> Sandbox:
    """The (cached) sandbox for ``agent_name`` rooted at ``agents_dir/<name>/workspace``."""
    key = (str(agents_dir), agent_name)
    sandbox = _SANDBOX_CACHE.get(key)
    if sandbox is None:
        sandbox = Sandbox(agents_dir / _safe_dir(agent_name) / "workspace")
        _SANDBOX_CACHE[key] = sandbox
    return sandbox


def _is_denied(path: Path) -> bool:
    """True if ``path`` is a secret-ish file that must not be read."""
    if ".git" in path.parts:
        return True
    return path.name in _DENY_NAMES or path.suffix.lower() in _DENY_SUFFIXES


def _looks_binary(sample: bytes) -> bool:
    """Heuristic: NUL byte, or >30% non-text bytes (the classic file(1) test)."""
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    non_text = sample.translate(None, _TEXT_BYTES)
    return len(non_text) / len(sample) > 0.30


def _err(message: str) -> dict[str, Any]:
    return {"status": "error", "error_message": message}


# --- read ---------------------------------------------------------------------------


def make_fs_read(agents_dir: Path) -> Callable[..., dict[str, Any]]:
    """Return the ADK ``fs_read`` tool bound to ``agents_dir``."""

    def fs_read(
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_bytes: int = MAX_READ_BYTES,
        include_line_numbers: bool = False,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Read a UTF-8 text file from the agent's workspace.

        Args:
            path (str): Workspace-relative (or absolute, inside the sandbox) file path.
            start_line (int): 1-based first line to return (default 1).
            end_line (int): 1-based last line to return; omit for end of file.
            max_bytes (int): Read at most this many bytes (capped at 10000000).
            include_line_numbers (bool): Prefix each returned line with its number.

        Returns:
            dict: On success {'status': 'success', 'content': str, 'total_lines': int,
            'start_line': int, 'end_line': int, 'truncated': bool}. On failure
            {'status': 'error', 'error_message': str}.
        """
        agent = tool_context.agent_name
        sandbox = _sandbox_for(agents_dir, agent)

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=READ, agent=agent, path=path, status=result["status"])
            return result

        try:
            target = sandbox.resolve(path)
        except SandboxError as exc:
            return done(_err(str(exc)))
        if _is_denied(target):
            return done(_err(f"reading {target.name} is not allowed"))
        if not target.is_file():
            return done(_err(f"not a file: {path}"))

        cap = max(1, min(max_bytes, MAX_READ_BYTES))
        with target.open("rb") as handle:
            data = handle.read(cap + 1)
        truncated = len(data) > cap
        data = data[:cap]
        if _looks_binary(data):
            return done(_err(f"file looks binary / not UTF-8: {path}"))

        lines = data.decode("utf-8", errors="replace").splitlines()
        total = len(lines)
        start = max(1, start_line)
        stop = total if end_line is None else min(end_line, total)
        selected = lines[start - 1 : stop] if start <= total else []
        if include_line_numbers:
            selected = [f"{start + i}\t{line}" for i, line in enumerate(selected)]
        return done(
            {
                "status": "success",
                "content": "\n".join(selected),
                "total_lines": total,
                "start_line": start,
                "end_line": stop,
                "truncated": truncated,
            }
        )

    return fs_read


# --- write --------------------------------------------------------------------------


def make_fs_write(agents_dir: Path) -> Callable[..., dict[str, Any]]:
    """Return the ADK ``fs_write`` tool bound to ``agents_dir``."""

    def fs_write(
        path: str,
        content: str,
        mode: str = "create",
        create_dirs: bool = False,
        backup: bool = False,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Write a text file in the agent's workspace.

        Args:
            path (str): Workspace-relative (or absolute, inside the sandbox) file path.
            content (str): Text to write.
            mode (str): 'create' (fail if it exists), 'overwrite', or 'append'.
            create_dirs (bool): Create missing parent directories.
            backup (bool): Copy an existing file to '<path>.bak' before overwriting.

        Returns:
            dict: On success {'status': 'success', 'path': str, 'bytes': int,
            'mode': str, 'created': bool}. On failure {'status': 'error',
            'error_message': str}.
        """
        agent = tool_context.agent_name
        sandbox = _sandbox_for(agents_dir, agent)

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=WRITE, agent=agent, path=path, status=result["status"])
            return result

        if mode not in _WRITE_MODES:
            return done(_err(f"mode must be one of: {', '.join(sorted(_WRITE_MODES))}"))
        try:
            target = sandbox.resolve(path)
        except SandboxError as exc:
            return done(_err(str(exc)))
        if target.is_dir():
            return done(_err(f"path is a directory: {path}"))

        existed = target.exists()
        if mode == "create" and existed:
            return done(_err(f"file already exists: {path}"))
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.is_dir():
            return done(_err(f"parent directory does not exist: {path}"))
        if backup and existed:
            shutil.copy2(target, target.with_name(target.name + ".bak"))

        if mode == "append":
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8")
        return done(
            {
                "status": "success",
                "path": str(target),
                "bytes": len(content.encode("utf-8")),
                "mode": mode,
                "created": not existed,
            }
        )

    return fs_write


# --- edit ---------------------------------------------------------------------------


def make_fs_edit(agents_dir: Path) -> Callable[..., dict[str, Any]]:
    """Return the ADK ``fs_edit`` tool bound to ``agents_dir``."""

    def fs_edit(
        path: str,
        old_string: str,
        new_string: str,
        expected_replacements: int = 1,
        dry_run: bool = False,
        backup: bool = False,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Replace text in a file, refusing unless the match count is exactly as expected.

        Args:
            path (str): Workspace-relative (or absolute, inside the sandbox) file path.
            old_string (str): Exact text to replace (must be non-empty).
            new_string (str): Replacement text.
            expected_replacements (int): Required number of occurrences (default 1); the
                edit is refused if the actual count differs.
            dry_run (bool): Report the would-be replacement count without writing.
            backup (bool): Copy the file to '<path>.bak' before editing.

        Returns:
            dict: On success {'status': 'success', 'replacements': int, 'dry_run': bool}.
            On failure {'status': 'error', 'error_message': str}.
        """
        agent = tool_context.agent_name
        sandbox = _sandbox_for(agents_dir, agent)

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=EDIT, agent=agent, path=path, status=result["status"])
            return result

        if not old_string:
            return done(_err("old_string must not be empty"))
        try:
            target = sandbox.resolve(path)
        except SandboxError as exc:
            return done(_err(str(exc)))
        if not target.is_file():
            return done(_err(f"not a file: {path}"))

        data = target.read_bytes()
        if _looks_binary(data):
            return done(_err(f"file looks binary / not UTF-8: {path}"))
        text = data.decode("utf-8", errors="replace")

        count = text.count(old_string)
        if count != expected_replacements:
            return done(
                _err(f"old_string matched {count} time(s), expected {expected_replacements}")
            )
        if dry_run:
            return done({"status": "success", "replacements": count, "dry_run": True})

        if backup:
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        target.write_text(text.replace(old_string, new_string), encoding="utf-8")
        return done({"status": "success", "replacements": count, "dry_run": False})

    return fs_edit


# --- glob / grep (external binaries) ------------------------------------------------


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` in ``cwd`` capturing text output, with a timeout."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT, check=False
    )


def make_fs_glob(agents_dir: Path) -> Callable[..., dict[str, Any]]:
    """Return the ADK ``fs_glob`` tool (requires the ``fd`` binary)."""

    def fs_glob(
        pattern: str,
        root: str | None = None,
        max_results: int = GLOB_MAX,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Find files matching a glob pattern under the workspace, using ``fd``.

        Args:
            pattern (str): Glob matched against the relative path (e.g. '**/*.py').
            root (str): Subdirectory to search; omit for the workspace root.
            max_results (int): Maximum matches to return (default 500).

        Returns:
            dict: On success {'status': 'success', 'matches': [str], 'count': int,
            'truncated': bool}. On failure {'status': 'error', 'error_message': str}.
        """
        agent = tool_context.agent_name
        sandbox = _sandbox_for(agents_dir, agent)

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=GLOB, agent=agent, pattern=pattern, status=result["status"])
            return result

        try:
            search = sandbox.resolve(root or ".")
        except SandboxError as exc:
            return done(_err(str(exc)))
        if not search.is_dir():
            return done(_err(f"not a directory: {root}"))

        cap = max(1, max_results)
        cmd = [
            "fd",
            "--glob",
            "--full-path",
            "--type",
            "f",
            "--max-results",
            str(cap + 1),
            "--",
            pattern,
        ]
        try:
            proc = _run(cmd, search)
        except (OSError, subprocess.SubprocessError) as exc:
            return done(_err(f"fd failed: {exc}"))
        if proc.returncode != 0:
            return done(_err(proc.stderr.strip() or "fd failed"))

        matches = [line for line in proc.stdout.splitlines() if line]
        truncated = len(matches) > cap
        return done(
            {
                "status": "success",
                "matches": matches[:cap],
                "count": min(len(matches), cap),
                "truncated": truncated,
            }
        )

    return fs_glob


def make_fs_grep(agents_dir: Path) -> Callable[..., dict[str, Any]]:
    """Return the ADK ``fs_grep`` tool (requires the ``rg`` binary)."""

    def fs_grep(
        pattern: str,
        regex: bool = False,
        case_sensitive: bool = False,
        context_lines: int = 0,
        root: str | None = None,
        glob: str | None = None,
        max_results: int = GREP_MAX,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Search file contents under the workspace, using ``rg`` (ripgrep).

        Output lines are 'path:line: text' for matches and 'path-line- text' for context.

        Args:
            pattern (str): Text (or regex if ``regex``) to search for.
            regex (bool): Treat ``pattern`` as a regular expression (default literal).
            case_sensitive (bool): Case-sensitive search (default insensitive).
            context_lines (int): Lines of context to show around each match.
            root (str): Subdirectory to search; omit for the workspace root.
            glob (str): Only search files matching this glob (e.g. '*.py').
            max_results (int): Maximum output lines to return (default 200).

        Returns:
            dict: On success {'status': 'success', 'matches': [str], 'count': int,
            'truncated': bool}. On failure {'status': 'error', 'error_message': str}.
        """
        agent = tool_context.agent_name
        sandbox = _sandbox_for(agents_dir, agent)

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=GREP, agent=agent, pattern=pattern, status=result["status"])
            return result

        try:
            search = sandbox.resolve(root or ".")
        except SandboxError as exc:
            return done(_err(str(exc)))
        if not search.is_dir():
            return done(_err(f"not a directory: {root}"))

        cmd = ["rg", "--line-number", "--with-filename", "--no-heading", "--color", "never"]
        if not regex:
            cmd.append("--fixed-strings")
        if not case_sensitive:
            cmd.append("--ignore-case")
        if context_lines > 0:
            cmd += ["--context", str(context_lines)]
        if glob:
            cmd += ["--glob", glob]
        cmd += ["--", pattern]
        try:
            proc = _run(cmd, search)
        except (OSError, subprocess.SubprocessError) as exc:
            return done(_err(f"rg failed: {exc}"))
        if proc.returncode not in (0, 1):  # rg exits 1 when there are simply no matches
            return done(_err(proc.stderr.strip() or "rg failed"))

        cap = max(1, max_results)
        lines = proc.stdout.splitlines()
        truncated = len(lines) > cap
        return done(
            {
                "status": "success",
                "matches": lines[:cap],
                "count": min(len(lines), cap),
                "truncated": truncated,
            }
        )

    return fs_grep

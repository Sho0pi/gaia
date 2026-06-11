"""The ``fs_grep`` tool: search file contents under the workspace using ``rg`` (ripgrep)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.fs.base import GREP_MAX, SandboxError, err, run_search, sandbox_for

NAME = "fs_grep"


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
        """Search file contents under the workspace.

        Output lines are 'path:line: text' for matches and 'path-line- text' for context.

        Args:
            pattern: text to find, or a regex when ``regex`` is true.
            regex: treat ``pattern`` as a regular expression (default literal text).
            case_sensitive: default is case-insensitive.
            context_lines: lines of context around each match.
            root: subdirectory to search; omit for the workspace root.
            glob: only search files matching this glob (e.g. '*.py').
        """
        agent = tool_context.agent_name
        sandbox = sandbox_for(agents_dir, agent)

        try:
            search = sandbox.resolve(root or ".")
        except SandboxError as exc:
            return err(str(exc))
        if not search.is_dir():
            return err(f"not a directory: {root}")

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
            proc = run_search(cmd, search)
        except (OSError, subprocess.SubprocessError) as exc:
            return err(f"rg failed: {exc}")
        if proc.returncode not in (0, 1):  # rg exits 1 when there are simply no matches
            return err(proc.stderr.strip() or "rg failed")

        cap = max(1, max_results)
        lines = proc.stdout.splitlines()
        truncated = len(lines) > cap
        return {
            "status": "success",
            "matches": lines[:cap],
            "count": min(len(lines), cap),
            "truncated": truncated,
        }

    return fs_grep

"""The ``fs_glob`` tool: find files under the workspace using the ``fd`` binary."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy.logs import log_event
from godpy.tools.fs.base import GLOB_MAX, SandboxError, err, run_search, sandbox_for

NAME = "fs_glob"


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
        sandbox = sandbox_for(agents_dir, agent)

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=NAME, agent=agent, pattern=pattern, status=result["status"])
            return result

        try:
            search = sandbox.resolve(root or ".")
        except SandboxError as exc:
            return done(err(str(exc)))
        if not search.is_dir():
            return done(err(f"not a directory: {root}"))

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
            proc = run_search(cmd, search)
        except (OSError, subprocess.SubprocessError) as exc:
            return done(err(f"fd failed: {exc}"))
        if proc.returncode != 0:
            return done(err(proc.stderr.strip() or "fd failed"))

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

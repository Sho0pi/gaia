"""The ``fs_read`` tool: read a UTF-8 text file from the agent's workspace."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.fs.base import (
    MAX_READ_BYTES,
    SandboxError,
    err,
    is_binary,
    is_denied,
    sandbox_for,
)

NAME = "fs_read"


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
        """Read UTF-8 text file from workspace.

        Args:
            path: workspace-relative file path.
            start_line: first line to return (1-based).
            end_line: last line to return; omit for end of file.
            include_line_numbers: prefix each line with number.
        """
        agent = tool_context.agent_name
        sandbox = sandbox_for(agents_dir, agent)

        try:
            target = sandbox.resolve(path)
        except SandboxError as exc:
            return err(str(exc))
        if is_denied(target):
            return err(f"reading {target.name} is not allowed")
        if not target.is_file():
            return err(f"not a file: {path}")

        cap = max(1, min(max_bytes, MAX_READ_BYTES))
        with target.open("rb") as handle:
            data = handle.read(cap + 1)
        truncated = len(data) > cap
        data = data[:cap]
        if is_binary(data):
            return err(f"file looks binary / not UTF-8: {path}")

        lines = data.decode("utf-8", errors="replace").splitlines()
        total = len(lines)
        start = max(1, start_line)
        stop = total if end_line is None else min(end_line, total)
        selected = lines[start - 1 : stop] if start <= total else []
        if include_line_numbers:
            selected = [f"{start + i}\t{line}" for i, line in enumerate(selected)]
        return {
            "status": "success",
            "content": "\n".join(selected),
            "total_lines": total,
            "start_line": start,
            "end_line": stop,
            "truncated": truncated,
        }

    return fs_read

"""The ``fs_write`` tool: create / overwrite / append a text file in the workspace."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.fs.base import SandboxError, err, sandbox_for

NAME = "fs_write"

_WRITE_MODES = frozenset({"create", "overwrite", "append"})


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
        """Write a text file in your workspace.

        Args:
            path: workspace-relative file path.
            mode: 'create' (fail if it exists), 'overwrite', or 'append'.
            create_dirs: create missing parent directories.
            backup: copy an existing file to '<path>.bak' before overwriting.
        """
        agent = tool_context.agent_name
        sandbox = sandbox_for(agents_dir, agent)

        if mode not in _WRITE_MODES:
            return err(f"mode must be one of: {', '.join(sorted(_WRITE_MODES))}")
        try:
            target = sandbox.resolve(path)
        except SandboxError as exc:
            return err(str(exc))
        if target.is_dir():
            return err(f"path is a directory: {path}")

        existed = target.exists()
        if mode == "create" and existed:
            return err(f"file already exists: {path}")
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.is_dir():
            return err(f"parent directory does not exist: {path}")
        if backup and existed:
            shutil.copy2(target, target.with_name(target.name + ".bak"))

        if mode == "append":
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8")
        return {
            "status": "success",
            "path": str(target),
            "bytes": len(content.encode("utf-8")),
            "mode": mode,
            "created": not existed,
        }

    return fs_write

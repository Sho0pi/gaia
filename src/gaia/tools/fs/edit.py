"""The ``fs_edit`` tool: replace text in a file with a uniqueness guard."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.fs.base import SandboxError, err, is_binary, sandbox_for

NAME = "fs_edit"


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
        """Replace text in workspace file; refuse unless match count exactly
        ``expected_replacements``.

        Args:
            path: workspace-relative file path.
            old_string: exact text to replace (non-empty).
            new_string: replacement text.
            dry_run: report would-be replacement count without writing.
            backup: copy file to '<path>.bak' before edit.
        """
        agent = tool_context.agent_name
        sandbox = sandbox_for(agents_dir, agent)

        if not old_string:
            return err("old_string must not be empty")
        try:
            target = sandbox.resolve(path)
        except SandboxError as exc:
            return err(str(exc))
        if not target.is_file():
            return err(f"not a file: {path}")

        data = target.read_bytes()
        if is_binary(data):
            return err(f"file looks binary / not UTF-8: {path}")
        text = data.decode("utf-8", errors="replace")

        count = text.count(old_string)
        if count != expected_replacements:
            return err(f"old_string matched {count} time(s), expected {expected_replacements}")
        if dry_run:
            return {"status": "success", "replacements": count, "dry_run": True}

        if backup:
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        target.write_text(text.replace(old_string, new_string), encoding="utf-8")
        return {"status": "success", "replacements": count, "dry_run": False}

    return fs_edit

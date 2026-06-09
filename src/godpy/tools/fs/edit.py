"""The ``fs_edit`` tool: replace text in a file with a uniqueness guard."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy.logs import log_event
from godpy.tools.fs.base import SandboxError, err, is_binary, sandbox_for

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
        sandbox = sandbox_for(agents_dir, agent)

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=NAME, agent=agent, path=path, status=result["status"])
            return result

        if not old_string:
            return done(err("old_string must not be empty"))
        try:
            target = sandbox.resolve(path)
        except SandboxError as exc:
            return done(err(str(exc)))
        if not target.is_file():
            return done(err(f"not a file: {path}"))

        data = target.read_bytes()
        if is_binary(data):
            return done(err(f"file looks binary / not UTF-8: {path}"))
        text = data.decode("utf-8", errors="replace")

        count = text.count(old_string)
        if count != expected_replacements:
            return done(
                err(f"old_string matched {count} time(s), expected {expected_replacements}")
            )
        if dry_run:
            return done({"status": "success", "replacements": count, "dry_run": True})

        if backup:
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        target.write_text(text.replace(old_string, new_string), encoding="utf-8")
        return done({"status": "success", "replacements": count, "dry_run": False})

    return fs_edit

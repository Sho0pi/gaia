"""capabilities: tell the agent what it can run, so it stops erroring into the sandbox.

The exec allowlist, the fs workspace boundary, and the serve rules are all enforced but invisible in
the per-tool docstrings — today the model only discovers them by failing (refused chaining, path
escapes workspace, serve outside the agents tree). This tool surfaces them on demand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext  # runtime: ADK reads the type hint

from gaia.tools._helpers import ok

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

NAME = "capabilities"


def make_capabilities(security: str, allowlist: tuple[str, ...]) -> Callable[..., dict[str, Any]]:
    """Return the ``capabilities`` tool, bound to the live exec security mode + allowlist."""

    def capabilities(*, tool_context: ToolContext) -> dict[str, Any]:
        """What you can run here — check this before exec/serve/fs when unsure, to avoid errors.

        Returns the allowed shell commands, your writable workspace path, and the serve rules, so
        you don't discover them by hitting a refusal. exec runs ONE command in allowlist mode (no
        chaining/pipes), and fs + serve stay inside your workspace.
        """
        from gaia import constants
        from gaia.tools.fs.base import sandbox_for

        agent = getattr(tool_context, "agent_name", "") or constants.APP_NAME
        workspace = sandbox_for(constants.AGENTS_DIR, agent).primary

        exec_caps: dict[str, Any] = {
            "security": security,
            "one_command_only": security == "allowlist",
            "no_chaining": ["&&", "||", "|", ";", "`", "$()"],
            "note": "run ONE command; the binary must exist on PATH. No chaining/pipes in "
            "allowlist mode — call exec once per command.",
        }
        if security == "allowlist":
            exec_caps["allowed_commands"] = sorted(allowlist)

        return ok(
            exec=exec_caps,
            workspace={
                "root": str(workspace),
                "rule": "fs reads/writes and serve must stay under this path (also writable: "
                "/tmp/gaia/<agent> and the uploads dir).",
            },
            serve={
                "rule": "serve a directory or .html UNDER your workspace; browser_navigate only "
                "opens loopback ports that serve or exec(port=) started.",
            },
        )

    return capabilities

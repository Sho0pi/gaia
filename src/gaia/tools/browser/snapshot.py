"""The ``browser_snapshot`` tool: read the current page as an accessibility tree."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err, snapshot_session

NAME = "browser_snapshot"


def make_browser_snapshot(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_snapshot`` tool bound to ``manager``."""

    async def browser_snapshot(*, tool_context: ToolContext) -> dict[str, Any]:
        """Read the current page as elements you can act on (its accessibility tree).

        Each element carries a ref like ``[ref=e4]``; pass that id (``e4``) to
        browser_click or browser_type. Snapshot again after the page changes — refs
        are reassigned each time.
        """
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            fields = await snapshot_session(session)
        except Exception as exc:
            return err(f"snapshot failed: {exc}")

        return {"status": "success", **fields}

    return browser_snapshot

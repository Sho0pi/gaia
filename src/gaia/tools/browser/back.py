"""The ``browser_back`` tool: go back to the previous page in history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err, ok_with_snapshot

NAME = "browser_back"


def make_browser_back(manager: BrowserSessionManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_back`` tool bound to ``manager``."""

    async def browser_back(*, tool_context: ToolContext) -> dict[str, Any]:
        """Go back to the previous page in browser history. Returns the updated page snapshot."""
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            await session.page.go_back()
        except Exception as exc:
            return err(f"back failed: {exc}")

        return await ok_with_snapshot(session)

    return browser_back

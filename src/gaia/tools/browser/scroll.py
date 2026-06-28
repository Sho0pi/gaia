"""The ``browser_scroll`` tool: scroll the current page up or down."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err, ok_with_snapshot

NAME = "browser_scroll"

#: Default scroll distance (~ one screen) when no amount is given.
_DEFAULT_PX = 600


def make_browser_scroll(manager: BrowserSessionManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_scroll`` tool bound to ``manager``."""

    async def browser_scroll(
        direction: str = "down",
        amount: int = 0,
        snapshot: bool = True,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Scroll the page to reveal more content. Returns the updated page snapshot.

        Args:
            direction: 'down' or 'up'.
            amount: pixels to scroll (0 = about one screen).
            snapshot: also return the updated page snapshot (default true); pass false to
                save tokens when you don't need the page back yet.
        """
        agent = tool_context.agent_name
        dy = amount if amount > 0 else _DEFAULT_PX
        if direction.strip().lower() == "up":
            dy = -dy

        try:
            session = await manager.get(agent)
            await session.page.mouse.wheel(0, dy)
        except Exception as exc:
            return err(f"scroll failed: {exc}")

        return await ok_with_snapshot(session, snapshot=snapshot)

    return browser_scroll

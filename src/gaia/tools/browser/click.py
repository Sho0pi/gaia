"""The ``browser_click`` tool: click an element by its snapshot ref."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import (
    BrowserError,
    BrowserSessionManager,
    err,
    ok_with_snapshot,
    resolve_locator,
)

NAME = "browser_click"


def make_browser_click(manager: BrowserSessionManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_click`` tool bound to ``manager``."""

    async def browser_click(ref: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Click an element on the current page. Returns the updated page snapshot.

        Args:
            ref: element ref from the most recent browser_snapshot, like 'e4'.
        """
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            locator = resolve_locator(session, ref.strip())
            await locator.click()
        except BrowserError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(f"click failed: {exc}")

        return await ok_with_snapshot(session)

    return browser_click

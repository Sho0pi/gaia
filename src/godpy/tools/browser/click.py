"""The ``browser_click`` tool: click an element by its snapshot ref."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy.logs import log_event
from godpy.tools.browser.base import BrowserError, BrowserSessionManager, err, resolve_locator

NAME = "browser_click"


def make_browser_click(manager: BrowserSessionManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_click`` tool bound to ``manager``."""

    async def browser_click(ref: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Click an element on the current page.

        Use a ref from the most recent browser_snapshot (e.g. ``e4``). Take a fresh
        snapshot afterwards, since clicking usually changes the page.

        Args:
            ref (str): The element ref from the last snapshot, like 'e4'.

        Returns:
            dict: On success {'status': 'success'}. On failure {'status': 'error',
            'error_message': str}.
        """
        agent = tool_context.agent_name

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=NAME, agent=agent, ref=ref, status=result["status"])
            return result

        try:
            session = await manager.get(agent)
            locator = resolve_locator(session, ref.strip())
            await locator.click()
        except BrowserError as exc:
            return done(err(str(exc)))
        except Exception as exc:
            return done(err(f"click failed: {exc}"))

        return done({"status": "success"})

    return browser_click

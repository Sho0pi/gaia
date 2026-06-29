"""The ``browser_console`` tool: read the page's console output + JS errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err

NAME = "browser_console"


def make_browser_console(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_console`` tool bound to ``manager``."""

    async def browser_console(*, tool_context: ToolContext) -> dict[str, Any]:
        """Get recent browser console output (log/warn/error) + uncaught JS errors.

        Returns the messages buffered since the last call, then clears the buffer — useful to
        diagnose why a page didn't behave (silent JS errors).
        """
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
        except Exception as exc:
            return err(f"console failed: {exc}")

        messages = list(session.console)
        session.console.clear()
        return {"status": "success", "messages": messages, "count": len(messages)}

    return browser_console

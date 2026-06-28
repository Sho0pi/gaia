"""The ``browser_press`` tool: press a keyboard key on the current page."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err

NAME = "browser_press"


def make_browser_press(manager: BrowserSessionManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_press`` tool bound to ``manager``."""

    async def browser_press(key: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Press a keyboard key on the current page (submit a form, navigate a list, etc.).

        Args:
            key: a Playwright key name, e.g. 'Enter', 'Tab', 'Escape', 'ArrowDown', 'PageDown'.
        """
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            await session.page.keyboard.press(key.strip())
        except Exception as exc:
            return err(f"press failed: {exc}")

        return {"status": "success"}

    return browser_press

"""The ``browser_evaluate`` tool: run JavaScript on the page (the escape hatch)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err

NAME = "browser_evaluate"

#: Cap the stringified result (token budget).
_RESULT_CAP = 5000


def make_browser_evaluate(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_evaluate`` tool bound to ``manager``."""

    async def browser_evaluate(expression: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Run JavaScript on the current page and return its result.

        The escape hatch for things the other tools don't cover (read a value, trigger a handler).

        Args:
            expression: a JS expression or arrow function — e.g. 'document.title' or
                '() => document.querySelectorAll("a").length'.
        """
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            result = await session.page.evaluate(expression)
        except Exception as exc:
            return err(f"evaluate failed: {exc}")

        text = str(result)
        cut = len(text) > _RESULT_CAP
        return {"status": "success", "result": text[:_RESULT_CAP], "truncated": cut}

    return browser_evaluate

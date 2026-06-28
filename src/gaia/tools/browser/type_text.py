"""The ``browser_type`` tool: type text into an element by its snapshot ref."""

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

NAME = "browser_type"


def make_browser_type(manager: BrowserSessionManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_type`` tool bound to ``manager``."""

    async def browser_type(
        ref: str, text: str, submit: bool = False, *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Type text into a field on the current page.

        Args:
            ref: element ref from the most recent browser_snapshot, like 'e2'.
            text: the text to type.
            submit: press Enter after typing (e.g. to run a search).
        """
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            locator = resolve_locator(session, ref.strip())
            await locator.fill(text)
            if submit:
                await locator.press("Enter")
        except BrowserError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(f"type failed: {exc}")

        return await ok_with_snapshot(session)

    return browser_type

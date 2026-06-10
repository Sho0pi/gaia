"""The ``browser_type`` tool: type text into an element by its snapshot ref."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy.tools.browser.base import BrowserError, BrowserSessionManager, err, resolve_locator

NAME = "browser_type"


def make_browser_type(manager: BrowserSessionManager) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_type`` tool bound to ``manager``."""

    async def browser_type(
        ref: str, text: str, submit: bool = False, *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Type text into a field on the current page.

        Use a ref from the most recent browser_snapshot (e.g. a textbox's ``e2``). Set
        submit=True to press Enter after typing (e.g. to run a search).

        Args:
            ref (str): The element ref from the last snapshot, like 'e2'.
            text (str): The text to type into the element.
            submit (bool): Press Enter after typing (default False).

        Returns:
            dict: On success {'status': 'success'}. On failure {'status': 'error',
            'error_message': str}.
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

        return {"status": "success"}

    return browser_type

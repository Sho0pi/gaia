"""The ``browser_dialog`` tool: respond to a native JS dialog (alert/confirm/prompt)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSession, BrowserSessionManager, err

NAME = "browser_dialog"

#: Keep references to in-flight accept/dismiss tasks so they aren't GC'd before they run.
_PENDING: set[asyncio.Task[Any]] = set()


def _arm(session: BrowserSession, *, accept: bool, text: str) -> None:
    """Arm a one-shot handler for the next native dialog (Playwright auto-dismisses otherwise)."""

    def on_dialog(dialog: Any) -> None:
        if accept:
            coro = dialog.accept(text) if text else dialog.accept()
        else:
            coro = dialog.dismiss()
        task = asyncio.ensure_future(coro)  # the dialog event handler is sync; resolve on the loop
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)

    session.page.once("dialog", on_dialog)


def make_browser_dialog(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_dialog`` tool bound to ``manager``."""

    async def browser_dialog(
        action: str = "accept", text: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Respond to the NEXT native JS dialog (alert / confirm / prompt / beforeunload).

        Call this just before the click/action that triggers the dialog.

        Args:
            action: 'accept' or 'dismiss'.
            text: text to enter for a prompt dialog (when accepting).
        """
        agent = tool_context.agent_name
        accept = action.strip().lower() != "dismiss"

        try:
            session = await manager.get(agent)
            _arm(session, accept=accept, text=text)
        except Exception as exc:
            return err(f"dialog failed: {exc}")

        return {"status": "success", "armed": "accept" if accept else "dismiss"}

    return browser_dialog

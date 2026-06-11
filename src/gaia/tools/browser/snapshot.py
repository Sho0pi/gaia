"""The ``browser_snapshot`` tool: read the current page as an accessibility tree."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, aria_snapshot, err, parse_refs, truncate

NAME = "browser_snapshot"


def make_browser_snapshot(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_snapshot`` tool bound to ``manager``."""

    async def browser_snapshot(*, tool_context: ToolContext) -> dict[str, Any]:
        """Read the current page as a list of elements you can act on.

        Returns the page's accessibility tree, where each element is tagged with a ref
        like ``[ref=e4]`` (e.g. ``- button "Search" [ref=e4]``). Pass that ref id
        (``e4``) to browser_click or browser_type. Call this after browser_navigate,
        and again after the page changes, since refs are reassigned each snapshot.

        Returns:
            dict: On success {'status': 'success', 'snapshot': str, 'url': str,
            'truncated': bool}. On failure {'status': 'error', 'error_message': str}.
        """
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            text = await aria_snapshot(session.page)
            session.refs = parse_refs(text)
            snapshot, was_truncated = truncate(text)
            url = str(session.page.url)
        except Exception as exc:
            return err(f"snapshot failed: {exc}")

        return {"status": "success", "snapshot": snapshot, "url": url, "truncated": was_truncated}

    return browser_snapshot

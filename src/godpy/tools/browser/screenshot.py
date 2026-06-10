"""The ``browser_screenshot`` tool: capture the current page as a PNG in the workspace."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from godpy import constants
from godpy.logs import log_event
from godpy.tools.browser.base import BrowserSessionManager, err
from godpy.tools.fs.base import sandbox_for

NAME = "browser_screenshot"


def make_browser_screenshot(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_screenshot`` tool bound to ``manager``."""

    async def browser_screenshot(
        full_page: bool = True, *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Capture a screenshot of the current page.

        Saves a PNG into your workspace and returns its path. Use this to show the user
        what a page looks like, or to verify a site you built renders correctly.

        Args:
            full_page (bool): Capture the entire scrollable page (default True). Set
                False to capture only the visible viewport.

        Returns:
            dict: On success {'status': 'success', 'path': str, 'url': str,
            'full_page': bool}. On failure {'status': 'error', 'error_message': str}.
        """
        agent = tool_context.agent_name

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event(
                "tool_used",
                tool=NAME,
                agent=agent,
                path=result.get("path"),
                status=result["status"],
            )
            return result

        # Land the PNG in the agent's own workspace (same dir the fs tools write to).
        workspace = sandbox_for(constants.AGENTS_DIR, agent).primary
        target: Path = workspace / f"screenshot-{int(time.time() * 1000)}.png"
        try:
            session = await manager.get(agent)
            await session.page.screenshot(path=str(target), full_page=full_page)
            url = str(session.page.url)
        except Exception as exc:
            return done(err(f"screenshot failed: {exc}"))

        return done({"status": "success", "path": str(target), "url": url, "full_page": full_page})

    return browser_screenshot

"""The ``browser_screenshot`` tool: capture the current page as a PNG in the workspace."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools.browser.base import (
    BrowserError,
    BrowserSessionManager,
    err,
    resolve_locator,
    settle_page,
)
from gaia.tools.fs.base import sandbox_for

NAME = "browser_screenshot"


def make_browser_screenshot(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_screenshot`` tool bound to ``manager``."""

    async def browser_screenshot(
        full_page: bool = False, ref: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Capture a screenshot of the current page and show it to the user.

        The image is delivered to the user automatically — do NOT also send_file it (that
        would send it twice).

        Args:
            full_page: default false = the visible viewport (a normal-aspect image, best for
                showing the user). True = the whole scrollable page (a tall image chat apps
                may crop) — use only when the user needs the entire page.
            ref: optional element ref from the last snapshot (e.g. 'e4') to capture
                just that element.
        """
        agent = tool_context.agent_name

        # Land the PNG in the agent's own workspace (same dir the fs tools write to).
        workspace = sandbox_for(constants.AGENTS_DIR, agent).primary
        target: Path = workspace / f"screenshot-{int(time.time() * 1000)}.png"
        try:
            session = await manager.get(agent)
            if ref.strip():
                # Screenshot a single element (the rest of the page is excluded).
                locator = resolve_locator(session, ref.strip())
                await locator.screenshot(path=str(target))
            else:
                await settle_page(session.page)  # let heavy SPAs finish painting (no blank shot)
                await session.page.screenshot(path=str(target), full_page=full_page)
            url = str(session.page.url)
        except BrowserError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(f"screenshot failed: {exc}")

        return {"status": "success", "path": str(target), "url": url}

    return browser_screenshot

"""The ``browser_get_images`` tool: list images on the current page."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err

NAME = "browser_get_images"

#: Cap how many images we return (token budget).
_MAX_IMAGES = 100

#: Collect each <img>'s effective source + alt text.
_JS = "() => Array.from(document.images).map(i => ({src: i.currentSrc || i.src, alt: i.alt || ''}))"


def make_browser_get_images(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_get_images`` tool bound to ``manager``."""

    async def browser_get_images(*, tool_context: ToolContext) -> dict[str, Any]:
        """List the images on the current page (their URLs and alt text)."""
        agent = tool_context.agent_name

        try:
            session = await manager.get(agent)
            images = await session.page.evaluate(_JS)
        except Exception as exc:
            return err(f"get_images failed: {exc}")

        images = images[:_MAX_IMAGES] if isinstance(images, list) else []
        return {"status": "success", "images": images, "count": len(images)}

    return browser_get_images

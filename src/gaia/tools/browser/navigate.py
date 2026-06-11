"""The ``browser_navigate`` tool: open a URL in the agent's headless browser."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools.browser.base import BrowserSessionManager, err
from gaia.tools.web_fetch import validate_url

NAME = "browser_navigate"


def make_browser_navigate(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_navigate`` tool bound to ``manager``."""

    async def browser_navigate(url: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Open a web page in your browser so you can read and interact with it.

        Starts a headless browser (or reuses the one already open) and loads the URL.
        Follow up with browser_snapshot to see the page, then browser_click /
        browser_type to interact, or browser_screenshot to capture it.

        Args:
            url (str): The http(s) URL to open.

        Returns:
            dict: On success {'status': 'success', 'url': <final URL>, 'title': str}.
            On failure {'status': 'error', 'error_message': str}.
        """
        cleaned = url.strip()
        agent = tool_context.agent_name

        if not cleaned:
            return err("url must not be empty")
        # Same SSRF guard web_fetch uses: reject loopback/private/metadata hosts.
        error = validate_url(cleaned)
        if error is not None:
            return err(error)

        try:
            session = await manager.get(agent)
            await session.page.goto(cleaned)
            final_url = str(session.page.url)
            # Re-validate after redirects: the landing host may differ from the input.
            redirected = validate_url(final_url)
            if redirected is not None:
                await manager.close(agent)
                return err(f"redirected to a blocked address: {redirected}")
            title = str(await session.page.title())
        except Exception as exc:
            return err(f"navigation failed: {exc}")

        return {"status": "success", "url": final_url, "title": title}

    return browser_navigate

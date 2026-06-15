"""The ``browser_navigate`` tool: open a URL in the agent's headless browser."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools.browser.base import BrowserSessionManager, err
from gaia.tools.web_fetch import validate_url

NAME = "browser_navigate"


def _local_workspace_file(url: str) -> str | None:
    """An error for a ``file://`` url that isn't a real file under ``AGENTS_DIR``, else ``None``.

    Gaia opens its own/souls' deliverables to preview them (e.g. a built ``index.html``); that
    is a trusted local file, not an SSRF target. Only ``file://`` paths resolving under
    ``AGENTS_DIR`` are allowed — anything else (``/etc/passwd``, ``..`` escapes) is rejected.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "file":
        return "only http(s), or a local file:// deliverable under the agents workspace"
    try:
        path = Path(unquote(parsed.path)).resolve()
        agents = constants.AGENTS_DIR.resolve()
    except OSError as exc:
        return f"bad file path: {exc}"
    if not path.is_relative_to(agents):
        return "file:// is only allowed for deliverables under the agents workspace"
    if not path.is_file():
        return f"no such file: {path}"
    return None


def make_browser_navigate(
    manager: BrowserSessionManager,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_navigate`` tool bound to ``manager``."""

    async def browser_navigate(url: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Open web page in browser to read/interact.

        Follow with browser_snapshot to see page, browser_click / browser_type
        to interact, or browser_screenshot to capture.

        Args:
            url: http(s) URL, or local ``file://`` path to workspace
                deliverable (e.g. built ``index.html``) to preview.
        """
        cleaned = url.strip()
        agent = tool_context.agent_name

        if not cleaned:
            return err("url must not be empty")
        # A local deliverable (file:// under the agents workspace) is allowed — Gaia previews
        # the sites its souls build. Everything else goes through the web SSRF guard.
        is_local = cleaned.lower().startswith("file:")
        error = _local_workspace_file(cleaned) if is_local else validate_url(cleaned)
        if error is not None:
            return err(error)

        try:
            session = await manager.get(agent)
            await session.page.goto(cleaned)
            final_url = str(session.page.url)
            # Re-validate after redirects: the landing host may differ from the input. A
            # local file that stays a local file is fine; otherwise apply the http guard.
            redirected = None if final_url.lower().startswith("file:") else validate_url(final_url)
            if redirected is not None:
                await manager.close(agent)
                return err(f"redirected to a blocked address: {redirected}")
            title = str(await session.page.title())
        except Exception as exc:
            return err(f"navigation failed: {exc}")

        return {"status": "success", "url": final_url, "title": title}

    return browser_navigate

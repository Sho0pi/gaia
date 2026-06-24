"""The ``browser_navigate`` tool: open a URL in the agent's headless browser."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools.browser.base import BrowserSessionManager, err
from gaia.tools.serve.base import ServedPorts
from gaia.tools.web_fetch import validate_url

NAME = "browser_navigate"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def _served_loopback(url: str, served: ServedPorts | None) -> bool:
    """True if ``url`` is an http(s) loopback url on a port we're actively serving.

    Lets the agent open its own ``serve``-d sites (``http://127.0.0.1:<port>``) past the
    SSRF guard, which otherwise blocks every loopback address. Only ports in ``served``
    pass — no other local service is reachable.
    """
    if served is None:
        return False
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or parsed.hostname not in _LOOPBACK_HOSTS:
        return False
    return parsed.port is not None and parsed.port in served


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
    served: ServedPorts | None = None,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``browser_navigate`` tool bound to ``manager``.

    ``served`` (the live set of ``serve``-d loopback ports) lets the agent open its own
    served sites past the SSRF guard; ``None`` means no served-port allowance.
    """

    async def browser_navigate(url: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Open a web page in your browser to read and interact with it.

        Follow up with browser_snapshot to see the page, browser_click / browser_type
        to interact, or browser_screenshot to capture it.

        Args:
            url: an http(s) URL, or a local ``file://`` path to one of your workspace
                deliverables (e.g. a built ``index.html``) to preview it.
        """
        url = url or ""  # a model may send null, not the default
        cleaned = url.strip()
        agent = tool_context.agent_name

        if not cleaned:
            return err("url must not be empty")
        # A local deliverable (file:// under the agents workspace) or one of our own served
        # loopback ports is allowed — Gaia previews the sites its souls build. Everything
        # else goes through the web SSRF guard.
        if cleaned.lower().startswith("file:"):
            error = _local_workspace_file(cleaned)
        elif _served_loopback(cleaned, served):
            error = None
        else:
            error = validate_url(cleaned)
        if error is not None:
            return err(error)

        try:
            session = await manager.get(agent)
            await session.page.goto(cleaned)
            final_url = str(session.page.url)
            # Re-validate after redirects: the landing host may differ from the input. A
            # local file or one of our served loopback ports is fine; else apply the guard.
            if final_url.lower().startswith("file:") or _served_loopback(final_url, served):
                redirected = None
            else:
                redirected = validate_url(final_url)
            if redirected is not None:
                await manager.close(agent)
                return err(f"redirected to a blocked address: {redirected}")
            title = str(await session.page.title())
        except Exception as exc:
            return err(f"navigation failed: {exc}")

        return {"status": "success", "url": final_url, "title": title}

    return browser_navigate

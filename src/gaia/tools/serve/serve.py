"""The ``serve_*`` tools: serve a soul's built site locally so it can be opened/shown.

``serve`` starts (or reuses) a local http server rooted at a workspace and returns its
url — open it with ``browser_navigate`` + ``browser_screenshot`` to show the user a real
render (not the blank ``file://`` one), or hand the url over for live testing. ``serve_stop``
and ``serve_list`` manage the running servers. All confined to ``AGENTS_DIR`` and bound to
loopback. (A future tool can wrap the returned url with a tunnel for a public temp link.)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from gaia.tools.serve.base import ServeError, StaticServerManager
from gaia.tools.serve.tunnel import TunnelError, TunnelManager

SERVE = "serve"
SERVE_STOP = "serve_stop"
SERVE_LIST = "serve_list"


def make_serve(
    manager: StaticServerManager,
    tunnel: TunnelManager | None = None,
    *,
    tunnel_enabled: bool = False,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``serve`` tool bound to ``manager`` (and an optional ``tunnel``)."""

    async def serve(path: str, public: bool = False) -> dict[str, Any]:
        """Serve a built site from a workspace over a local URL so you can open or show it.

        Pass a soul's workspace directory (or a specific .html file in it). Then open the
        returned ``url`` with browser_navigate and browser_screenshot to render it for the
        user — a real http render, unlike a blank file:// one. The server stays up (for
        live testing) until idle or serve_stop.

        Set public=True to also expose it on a public https URL (public_url) the user can
        open on their phone or share — only when public tunneling is enabled by the admin.

        Args:
            path: absolute path to a workspace directory under the agents tree, or an
                .html file inside one.
            public: also open a public https URL for the site (off unless enabled).
        """
        try:
            site, url = await manager.serve(path.strip())
        except ServeError as exc:
            return {"status": "error", "error_message": str(exc)}
        except Exception as exc:  # tools never raise to the model
            return {"status": "error", "error_message": f"could not serve: {exc}"}

        result: dict[str, Any] = {
            "status": "success",
            "url": url,
            "port": site.port,
            "root": str(site.root),
        }
        if public:
            if not tunnel_enabled or tunnel is None:
                result["public_url_error"] = (
                    "public tunneling is disabled — set tools.serve.tunnel.enabled in gaia.yaml"
                )
            else:
                try:
                    result["public_url"] = await tunnel.open(site.port)
                except TunnelError as exc:
                    result["public_url_error"] = str(exc)
        return result

    return serve


def make_serve_stop(
    manager: StaticServerManager, tunnel: TunnelManager | None = None
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``serve_stop`` tool bound to ``manager`` (and an optional ``tunnel``)."""

    async def serve_stop(target: str) -> dict[str, Any]:
        """Stop a local server started with serve (and its public tunnel, if any).

        Args:
            target: the port number, or the workspace path, of the server to stop.
        """
        try:
            site = await manager.stop(target.strip())
        except Exception as exc:  # tools never raise to the model
            return {"status": "error", "error_message": f"could not stop: {exc}"}
        if site is None:
            return {"status": "error", "error_message": f"no server matching {target.strip()!r}"}
        if tunnel is not None:
            await tunnel.close(site.port)
        return {"status": "success", "stopped": str(site.root), "port": site.port}

    return serve_stop


def make_serve_list(
    manager: StaticServerManager, tunnel: TunnelManager | None = None
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``serve_list`` tool bound to ``manager`` (and an optional ``tunnel``)."""

    async def serve_list() -> dict[str, Any]:
        """List the local sites you're currently serving (url, port, public_url, workspace)."""
        sites = []
        for s in manager.list():
            entry: dict[str, Any] = {"url": s.url, "port": s.port, "root": str(s.root)}
            live = tunnel.get(s.port) if tunnel is not None else None
            if live is not None:
                entry["public_url"] = live.url
            sites.append(entry)
        return {"status": "success", "servers": sites}

    return serve_list

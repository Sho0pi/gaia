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

from gaia.connectors.base import current_chat
from gaia.tools._helpers import err, ok
from gaia.tools.serve.base import ServeError, StaticServerManager
from gaia.tools.serve.tunnel import TunnelError, TunnelManager

SERVE = "serve"
SERVE_STOP = "serve_stop"
SERVE_LIST = "serve_list"

#: Channels where the user is at the local machine and can open 127.0.0.1 directly, so a
#: served site stays private by default. Everyone else (whatsapp/telegram/…) is remote and
#: needs a public URL, so serving defaults to public for them.
_LOCAL_CHANNELS = frozenset({"", "cli", "socket"})


def _auto_public() -> bool:
    """Default for ``public``: private when the user is local (cli), public when remote."""
    return current_chat.get()[0] not in _LOCAL_CHANNELS


def make_serve(
    manager: StaticServerManager,
    tunnel: TunnelManager | None = None,
    *,
    tunnel_enabled: bool = False,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``serve`` tool bound to ``manager`` (and an optional ``tunnel``)."""

    async def serve(path: str, public: bool | None = None) -> dict[str, Any]:
        """Serve a built site from a workspace so you can open, screenshot, or share it.

        Pass a soul's workspace directory (or a specific .html file in it). Open the
        returned ``url`` with browser_navigate and browser_screenshot to render it — a real
        http render, unlike a blank file:// one. The server stays up (for live testing)
        until idle or serve_stop.

        By default the site is also exposed on a public https URL (``public_url``) when the
        user is messaging from a phone/remote channel, and kept local-only when they're at
        the computer (cli). Pass public=True/False to override.

        Args:
            path: absolute path to a workspace directory under the agents tree, or an
                .html file inside one.
            public: force a public URL on/off; omit to auto-pick by channel.
        """
        try:
            site, url = await manager.serve(path.strip())
        except ServeError as exc:
            return err(str(exc))
        except Exception as exc:  # tools never raise to the model
            return err(f"could not serve: {exc}")

        result: dict[str, Any] = {
            "status": "success",
            "url": url,
            "port": site.port,
            "root": str(site.root),
        }
        want_public = _auto_public() if public is None else public
        if want_public:
            if tunnel_enabled and tunnel is not None:
                try:
                    # The tunnel forwards the port root; re-attach the entry (e.g. "site.html")
                    # so the public link opens the same page the local url/screenshot does — not
                    # the bare directory (a listing or 404 when there's no index.html).
                    base = (await tunnel.open(site.port)).rstrip("/")
                    result["public_url"] = base + "/" + url[len(site.url) :]
                except TunnelError as exc:
                    result["public_url_error"] = str(exc)
            elif public is True:
                # Only surface the "disabled" note when the model explicitly asked for it;
                # an auto-public on a deployment with tunneling off just stays local.
                result["public_url_error"] = (
                    "public tunneling is disabled — set tools.serve.tunnel.enabled in gaia.yaml"
                )
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
            return err(f"could not stop: {exc}")
        if site is None:
            return err(f"no server matching {target.strip()!r}")
        if tunnel is not None:
            await tunnel.close(site.port)
        return ok(stopped=str(site.root), port=site.port)

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
        return ok(servers=sites)

    return serve_list

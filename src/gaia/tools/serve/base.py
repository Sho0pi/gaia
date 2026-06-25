"""Shared foundation for the ``serve_*`` tools: a local static file server per workspace.

A soul builds a website in its workspace; opening it as ``file://`` renders blank for any
real site (Chromium blocks ES modules / ``fetch`` on the file origin and resolves
root-absolute refs like ``/style.css`` to the filesystem root). Serving the workspace over
``http://127.0.0.1:<port>`` lifts all of that. :class:`StaticServerManager` owns those
servers — one per workspace dir, on an ephemeral loopback port, with an idle reaper —
mirroring :class:`gaia.tools.shell.base.ProcessManager`.

:class:`ServedPorts` is the small set of ports we're actively serving. It is shared with
:func:`gaia.tools.browser.navigate.make_browser_navigate`, whose SSRF guard otherwise
blocks every loopback address: navigate trusts an ``http://127.0.0.1:<port>`` url **iff**
``port`` is in this set, so the model can open our own served sites but no other local
service.
"""

from __future__ import annotations

import asyncio
import atexit
import functools
import os
import threading
import time
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gaia import constants

#: Default seconds a server may sit idle (no request) before the reaper stops it. Matches
#: the browser session idle window.
DEFAULT_IDLE_SECONDS = 600.0

#: How often the reaper wakes to check for idle servers.
_REAP_INTERVAL = 30.0


class ServeError(Exception):
    """Raised when a path can't be served (escapes AGENTS_DIR, missing, not a dir)."""


class ServedPorts:
    """The set of loopback ports currently served — read by browser_navigate's SSRF guard."""

    def __init__(self) -> None:
        self._ports: set[int] = set()

    def add(self, port: int) -> None:
        self._ports.add(port)

    def discard(self, port: int) -> None:
        self._ports.discard(port)

    def __contains__(self, port: object) -> bool:
        return port in self._ports


@dataclass
class ServedSite:
    """One running static server: the workspace root it serves on a loopback port."""

    root: Path
    port: int
    httpd: ThreadingHTTPServer
    thread: threading.Thread
    last_access: float = field(default_factory=time.monotonic)

    @property
    def url(self) -> str:
        """The base url of this server (trailing slash)."""
        return f"http://127.0.0.1:{self.port}/"


def _resolve_under_agents(path: str) -> tuple[Path, str]:
    """Resolve ``path`` to a (dir-to-serve, entry-relative-to-dir) under ``AGENTS_DIR``.

    A directory serves itself (entry ``""``); a file serves its parent and points at the
    file. Realpath-resolved and confined to ``AGENTS_DIR`` so a soul deliverable can be
    served but nothing else on disk can.

    A **relative** path resolves against the caller's workspace (like the fs tools), not the
    process cwd — so the model can pass ``index.html`` / ``site`` the same way it does everywhere
    else, instead of being forced to discover an absolute ``~/.gaia/agents/<agent>/workspace`` path.
    """
    from gaia.tools.fs.base import current_agent, sandbox_for

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = sandbox_for(constants.AGENTS_DIR, current_agent.get()).primary / path
    try:
        target = Path(os.path.realpath(candidate))
        agents = Path(os.path.realpath(constants.AGENTS_DIR))
    except OSError as exc:  # pragma: no cover - defensive
        raise ServeError(f"bad path: {exc}") from exc
    if not target.is_relative_to(agents):
        raise ServeError("can only serve a directory under the agents workspace")
    if target.is_dir():
        return target, ""
    if target.is_file():
        return target.parent, target.name
    raise ServeError(f"no such path: {target}")


def _make_handler(site_ref: dict[str, ServedSite]) -> type[SimpleHTTPRequestHandler]:
    """A quiet SimpleHTTPRequestHandler that stamps last-access on the owning site."""

    class _Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # silence stdout access logs
            return None

        def handle_one_request(self) -> None:
            site = site_ref.get("site")
            if site is not None:
                site.last_access = time.monotonic()
            super().handle_one_request()

    return _Handler


class StaticServerManager:
    """Owns the per-workspace static servers and guarantees they're cleaned up.

    One instance per tool registry (shared by the serve tools). Servers are keyed by their
    resolved root dir, so serving the same workspace twice reuses the running server. An
    idle reaper stops servers no one has hit recently; ``close_all`` (and an atexit hook)
    tears everything down.
    """

    def __init__(self, served: ServedPorts, *, idle_seconds: float = DEFAULT_IDLE_SECONDS) -> None:
        self._served = served
        self._idle = idle_seconds
        self._sites: dict[Path, ServedSite] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._cleanup_registered = False

    async def serve(self, path: str) -> tuple[ServedSite, str]:
        """Serve the workspace at ``path``; return the site and the full url to its entry."""
        root, entry = _resolve_under_agents(path)
        site = self._sites.get(root)
        if site is None:
            site = self._start(root)
            self._sites[root] = site
            self._served.add(site.port)
            self._ensure_reaper()
            self._register_cleanup_once()
        site.last_access = time.monotonic()
        return site, site.url + entry

    def _start(self, root: Path) -> ServedSite:
        ref: dict[str, ServedSite] = {}
        handler = functools.partial(_make_handler(ref), directory=str(root))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True, name=f"serve-{port}")
        thread.start()
        site = ServedSite(root=root, port=port, httpd=httpd, thread=thread)
        ref["site"] = site  # let the handler stamp last_access on this site
        return site

    def list(self) -> list[ServedSite]:
        """Every running server, in start order."""
        return list(self._sites.values())

    async def stop(self, target: str) -> ServedSite | None:
        """Stop the server matching ``target`` (a port or served path); ``None`` if no match."""
        site = self._find(target)
        if site is None:
            return None
        await self._stop_site(site)
        return site

    def _find(self, target: str) -> ServedSite | None:
        t = target.strip()
        if t.isdigit():
            return next((s for s in self._sites.values() if s.port == int(t)), None)
        try:
            root, _ = _resolve_under_agents(t)
        except ServeError:
            return None
        return self._sites.get(root)

    async def _stop_site(self, site: ServedSite) -> None:
        self._served.discard(site.port)
        self._sites.pop(site.root, None)
        # shutdown() blocks until serve_forever returns; run it off the event loop.
        await asyncio.to_thread(site.httpd.shutdown)
        site.httpd.server_close()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        """Stop servers idle longer than ``idle_seconds`` (best-effort, runs while any live)."""
        while self._sites:
            await asyncio.sleep(_REAP_INTERVAL)
            now = time.monotonic()
            stale = [s for s in self._sites.values() if now - s.last_access > self._idle]
            for site in stale:
                await self._stop_site(site)

    def _register_cleanup_once(self) -> None:
        if not self._cleanup_registered:
            atexit.register(self._cleanup_at_exit)
            self._cleanup_registered = True

    def _cleanup_at_exit(self) -> None:  # pragma: no cover - shutdown best-effort
        if not self._sites:
            return
        for site in list(self._sites.values()):
            self._served.discard(site.port)
            try:
                site.httpd.shutdown()
                site.httpd.server_close()
            except Exception:
                pass
        self._sites.clear()

    async def close_all(self) -> None:
        """Stop every server; called by Gaia.close on the live loop."""
        if self._reaper is not None and not self._reaper.done():
            self._reaper.cancel()
        for site in list(self._sites.values()):
            try:
                await self._stop_site(site)
            except Exception:  # pragma: no cover - shutdown best-effort
                pass

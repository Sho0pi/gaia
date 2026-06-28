"""Shared foundation for the ``browser_*`` tools: the per-agent session + a11y snapshot.

A browser is *stateful* — ``browser_navigate`` opens a page, and later
``browser_snapshot`` / ``browser_click`` act on that same live page. So unlike the
stateless ``fs_*`` / web tools, the browser tools share a :class:`BrowserSession` per
agent, held by a :class:`BrowserSessionManager` keyed on ``tool_context.agent_name``
(one soul's browser never bleeds into another's).

Sessions are created lazily on first use, swept after an idle timeout, and closed on
process exit (``atexit``) so no headless Chromium ever orphans. Playwright is imported
lazily inside the default launcher (heavy-deps convention) — importing this module
needs neither Playwright nor a browser binary, which keeps it unit-testable with a
fake page.
"""

from __future__ import annotations

import asyncio
import atexit
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from gaia.tools._helpers import err as err  # re-export for gaia.tools.browser.* importers

#: Snapshot text is capped before it goes to the model (deterministic token budget).
SNAPSHOT_CHAR_CAP = 8000

#: Playwright's ``aria_snapshot(mode="ai")`` tags each element with ``[ref=eN]``; this
#: pulls those ref ids out so click/type can validate a ref before resolving it.
_REF_RE = re.compile(r"\[ref=(e\d+)\]")

#: Close a session after this many seconds without use.
IDLE_TIMEOUT_SECONDS = 600.0

#: Keep at most this many recent console/pageerror lines per session (browser_console reads them).
CONSOLE_CAP = 200

#: A launcher opens a fresh page and returns it with a coroutine that tears it down.
Launcher = Callable[[], Awaitable[tuple[Any, Callable[[], Awaitable[None]]]]]


class BrowserError(Exception):
    """Raised for a browser operation that should surface as a tool error dict."""


@dataclass
class BrowserSession:
    """One agent's live page plus the ``eN`` refs its last snapshot handed out."""

    page: Any
    close: Callable[[], Awaitable[None]]
    #: ref ids (``e1``, ``e2``, …) from the last snapshot; how click/type target.
    refs: set[str] = field(default_factory=set)
    #: Recent console messages + uncaught JS errors (capped); browser_console reads + clears them.
    console: list[str] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def record_console(self, line: str) -> None:
        """Append a console/pageerror line, dropping the oldest past :data:`CONSOLE_CAP`."""
        self.console.append(line)
        if len(self.console) > CONSOLE_CAP:
            del self.console[:-CONSOLE_CAP]


async def _playwright_launcher() -> tuple[Any, Callable[[], Awaitable[None]]]:
    """Default launcher: start Playwright, open a headless Chromium page.

    Imports Playwright lazily so the module imports without it. Returns the page and a
    close coroutine that tears the whole stack (page → context → browser → playwright)
    down in order.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()

    async def close() -> None:
        await context.close()
        await browser.close()
        await pw.stop()

    return page, close


async def _is_alive(session: BrowserSession) -> bool:
    """Cheap liveness probe: a dead browser/driver raises on any op, telling us to relaunch."""
    try:
        await session.page.evaluate("1")
    except Exception:
        return False
    return True


def _camoufox_opts(browser_cfg: Any) -> dict[str, Any]:
    """Build AsyncCamoufox kwargs from BrowserConfig (omitting unset ones → Camoufox defaults)."""
    opts: dict[str, Any] = {
        "headless": getattr(browser_cfg, "headless", True),
        "humanize": getattr(browser_cfg, "humanize", True),  # human-like cursor (stealth)
    }
    if geoip := getattr(browser_cfg, "geoip", False):
        opts["geoip"] = geoip
    if locale := getattr(browser_cfg, "locale", ""):
        opts["locale"] = locale
    if os_ := getattr(browser_cfg, "os", ""):
        opts["os"] = os_
    if getattr(browser_cfg, "block_images", False):
        opts["block_images"] = True
    return opts


def make_launcher(browser_cfg: Any) -> Launcher:
    """Pick the native launcher for ``browser_cfg.engine``: Camoufox (default) or chromium.

    Camoufox is a drop-in Playwright firefox (anti-detect); it slots into the same seam so every
    browser tool is engine-agnostic. ``camoufox`` is imported lazily (optional dep).
    """
    if getattr(browser_cfg, "engine", "camoufox") != "camoufox":
        return _playwright_launcher
    opts = _camoufox_opts(browser_cfg)

    async def launch() -> tuple[Any, Callable[[], Awaitable[None]]]:
        from camoufox.async_api import AsyncCamoufox

        cam = AsyncCamoufox(**opts)  # type: ignore[no-untyped-call]  # camoufox ships no stubs
        browser = await cam.__aenter__()
        page = await browser.new_page()

        async def close() -> None:
            await cam.__aexit__(None, None, None)

        return page, close

    return launch


class BrowserSessionManager:
    """Owns each agent's browser session and guarantees they're closed.

    One instance is created by the tool registry and shared by the browser tools (each
    tool closure captures it) — so there is no module-level singleton. On its first
    session it registers a single ``atexit`` hook to close any still-open browsers when
    the process exits, so a headless Chromium can't orphan.
    """

    def __init__(
        self, launcher: Launcher | None = None, *, idle_timeout: float = IDLE_TIMEOUT_SECONDS
    ) -> None:
        self._launcher = launcher or _playwright_launcher
        self._idle_timeout = idle_timeout
        self._sessions: dict[str, BrowserSession] = {}
        self._cleanup_registered = False

    def _register_cleanup_once(self) -> None:
        """Arrange for ``close_all`` to run on process exit (idempotent, lazy)."""
        if not self._cleanup_registered:
            atexit.register(self._cleanup_at_exit)
            self._cleanup_registered = True

    def _cleanup_at_exit(self) -> None:
        """atexit hook: close any still-open sessions. Best-effort."""
        if not self._sessions:
            return
        try:
            asyncio.run(self.close_all())
        except Exception:  # pragma: no cover - shutdown best-effort
            pass

    async def get(self, agent: str) -> BrowserSession:
        """Return ``agent``'s session, opening one on first use. Sweeps idle sessions.

        A cached session whose browser has died (a crash, an OOM, a dropped driver connection —
        Camoufox/Chromium do this on heavy pages) is dropped and relaunched, so the browser
        self-heals on the next call instead of every later call failing "Connection closed".
        """
        await self._sweep_idle()
        session = self._sessions.get(agent)
        if session is not None and not await _is_alive(session):
            await self.close(agent)
            session = None
        if session is None:
            self._register_cleanup_once()
            page, close = await self._launcher()
            session = BrowserSession(page=page, close=close)
            # Buffer console output + uncaught JS errors so browser_console can report them.
            page.on("console", lambda m: session.record_console(f"[{m.type}] {m.text}"))
            page.on("pageerror", lambda e: session.record_console(f"[pageerror] {e}"))
            self._sessions[agent] = session
        session.touch()
        return session

    async def close(self, agent: str) -> None:
        """Close and forget ``agent``'s session (no-op if none open). Best-effort — closing a
        crashed browser may itself raise, but the session is dropped regardless."""
        session = self._sessions.pop(agent, None)
        if session is not None:
            with suppress(Exception):
                await session.close()

    async def close_all(self) -> None:
        """Close every open session; called on process exit."""
        for agent in list(self._sessions):
            await self.close(agent)

    async def _sweep_idle(self) -> None:
        now = time.monotonic()
        stale = [a for a, s in self._sessions.items() if now - s.last_used > self._idle_timeout]
        for agent in stale:
            await self.close(agent)


async def aria_snapshot(page: Any) -> str:
    """Return the page's accessibility tree as Playwright's AI-mode aria snapshot.

    The ``mode="ai"`` text tags each element with ``[ref=eN]`` — the stable handle the
    action tools resolve via an ``aria-ref=eN`` locator.
    """
    return str(await page.locator("body").aria_snapshot(mode="ai"))


def parse_refs(snapshot: str) -> set[str]:
    """Pull the ``eN`` ref ids out of an AI-mode aria snapshot."""
    return set(_REF_RE.findall(snapshot))


def normalize_ref(ref: str) -> str:
    """Accept ``e4`` or ``@e4`` (a stray leading ``@``) → ``e4``."""
    return ref.strip().lstrip("@")


def resolve_locator(session: BrowserSession, ref: str) -> Any:
    """Resolve a snapshot ref (``e4``) to a Playwright locator, or raise.

    The action tools target elements by the ref the last snapshot handed out, so a ref
    that isn't in the current set (stale snapshot, typo) is a clear error rather than a
    silent miss.
    """
    norm = normalize_ref(ref)
    if norm not in session.refs:
        raise BrowserError(f"unknown ref {ref!r}; call browser_snapshot first")
    return session.page.locator(f"aria-ref={norm}")


def truncate(text: str, cap: int = SNAPSHOT_CHAR_CAP) -> tuple[str, bool]:
    """Return ``text`` capped to ``cap`` chars and whether it was cut."""
    if len(text) <= cap:
        return text, False
    return text[:cap], True


async def snapshot_session(session: BrowserSession) -> dict[str, Any]:
    """Fresh aria snapshot of the session's page; refresh its refs; return the result fields."""
    text = await aria_snapshot(session.page)
    session.refs = parse_refs(text)
    snapshot, truncated = truncate(text)
    return {"snapshot": snapshot, "truncated": truncated, "url": str(session.page.url)}


async def ok_with_snapshot(session: BrowserSession, **extra: Any) -> dict[str, Any]:
    """A success result that also carries the post-action snapshot (#90 — saves a snapshot turn).

    The model almost always wants the page after an action, and refs are reassigned, so each action
    tool folds the new snapshot into its result. Best-effort: if the snapshot can't be taken (page
    mid-navigation) the action still reports success — the model can ``browser_snapshot`` then.
    """
    result: dict[str, Any] = {"status": "success", **extra}
    try:
        result.update(await snapshot_session(session))
    except Exception:  # pragma: no cover - the action succeeded; the snapshot is a bonus
        pass
    return result

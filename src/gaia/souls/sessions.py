"""Warm soul sessions: keep a soul's ADK session alive between delegations.

Each ``delegate_to_soul`` used to build a throwaway ``InMemorySessionService``, so the soul lost
its working context and re-read its whole workspace every time. :class:`SoulSessionManager` keeps
one live session per ``(soul, project)`` — created on first use, reused after — so a
re-delegation resumes where it left off. An idle reaper evicts a session no one has touched
recently (the next call rebuilds it cold), mirroring
:class:`gaia.tools.serve.base.StaticServerManager`.

A session can run only one turn at a time (ADK appends to it serially), and the missions
dispatcher runs tasks concurrently, so each warm session carries its own lock — the caller runs
under it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.sessions import InMemorySessionService

logger = logging.getLogger(__name__)

#: How often the reaper wakes to look for idle sessions.
_REAP_INTERVAL = 60.0


@dataclass
class WarmSession:
    """One soul's live ADK session: session service, session id, a turn lock, last-touch time."""

    session_service: InMemorySessionService
    session_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_access: float = field(default_factory=time.monotonic)


class SoulSessionManager:
    """Owns the warm per-``(soul, project)`` sessions and evicts the idle ones.

    One instance per :class:`~gaia.core.agent.Gaia` (a DI singleton). ``idle_seconds`` is read
    from a callable each reap so a ``gaia.yaml`` edit (``souls.session_idle_minutes``) takes effect
    without a restart.
    """

    def __init__(self, idle_seconds: float | Callable[[], float] = 1800.0) -> None:
        self._idle = idle_seconds
        self._sessions: dict[str, WarmSession] = {}
        self._reaper: asyncio.Task[None] | None = None

    async def acquire(
        self, key: str, *, app_name: str, user_id: str, state: dict[str, Any] | None = None
    ) -> WarmSession:
        """Return the warm session for ``key`` (``soul/project``), creating it on first use.

        ``state`` seeds the ADK session only when it is first created; a reused session keeps its
        accumulated events (that's the point — the soul's memory). Run the turn under
        ``WarmSession.lock``.
        """
        warm = self._sessions.get(key)
        if warm is None:
            from google.adk.sessions import InMemorySessionService

            session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
            session_id = f"soul-{key}"
            await session_service.create_session(
                app_name=app_name, user_id=user_id, session_id=session_id, state=state or {}
            )
            warm = WarmSession(session_service=session_service, session_id=session_id)
            self._sessions[key] = warm
            self._ensure_reaper()
            logger.info("soul session started: %s (warm, %d live)", key, len(self._sessions))
        else:
            logger.debug("soul session resumed: %s", key)
        warm.last_access = time.monotonic()
        return warm

    def active(self) -> list[tuple[str, float]]:
        """Live sessions as ``(key, idle_seconds)``, most-recently-used first — for ``/souls``."""
        now = time.monotonic()
        rows = [(key, now - w.last_access) for key, w in self._sessions.items()]
        return sorted(rows, key=lambda r: r[1])

    def _idle_seconds(self) -> float:
        return self._idle() if callable(self._idle) else self._idle

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        """Evict sessions idle longer than the configured window (runs while any are live)."""
        while self._sessions:
            await asyncio.sleep(_REAP_INTERVAL)
            self._evict_idle()

    def _evict_idle(self) -> None:
        """Drop every session untouched for longer than the idle window; next call rebuilds cold."""
        cutoff = self._idle_seconds()
        now = time.monotonic()
        for key in [k for k, w in self._sessions.items() if now - w.last_access > cutoff]:
            self._sessions.pop(key, None)
            logger.info("soul session evicted (idle): %s", key)

    async def close_all(self) -> None:
        """Cancel the reaper and drop every session; called by Gaia.close on the live loop."""
        if self._reaper is not None and not self._reaper.done():
            self._reaper.cancel()
        if self._sessions:
            logger.info("closing %d warm soul session(s)", len(self._sessions))
        self._sessions.clear()

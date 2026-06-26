"""SoulSessionManager: warm per-(soul, project) sessions with idle eviction."""

from __future__ import annotations

import asyncio
import time

from gaia import constants
from gaia.souls.sessions import SoulSessionManager


async def test_acquire_reuses_same_session_per_key() -> None:
    mgr = SoulSessionManager()
    a = await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")
    b = await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")

    assert a is b  # same warm session reused → the soul resumes, not restarts
    assert a.session_id == "soul-writer/p1"


async def test_acquire_separate_session_per_key() -> None:
    mgr = SoulSessionManager()
    a = await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")
    b = await mgr.acquire("writer/p2", app_name=constants.APP_NAME, user_id="u")
    c = await mgr.acquire("artist/p1", app_name=constants.APP_NAME, user_id="u")

    assert a is not b and a is not c and b is not c
    assert len(mgr._sessions) == 3


async def test_idle_session_is_evicted() -> None:
    mgr = SoulSessionManager(idle_seconds=600.0)
    warm = await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")
    warm.last_access = time.monotonic() - 1000  # older than the idle window

    mgr._evict_idle()

    assert mgr._sessions == {}
    # a fresh acquire rebuilds a new (cold) session
    again = await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")
    assert again is not warm


async def test_idle_window_reads_a_live_callable() -> None:
    # The DI wiring passes a callable so a gaia.yaml edit changes the window without a restart.
    cutoff = {"s": 600.0}
    mgr = SoulSessionManager(idle_seconds=lambda: cutoff["s"])
    warm = await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")
    warm.last_access = time.monotonic() - 100

    mgr._evict_idle()  # 100s idle < 600s window → kept
    assert "writer/p1" in mgr._sessions

    cutoff["s"] = 60.0  # tighten the window live
    mgr._evict_idle()  # now 100s idle > 60s → evicted
    assert mgr._sessions == {}


async def test_pinned_session_survives_the_reaper() -> None:
    # A soul paused on ask_user pins its session so the reaper can't drop it before the user
    # answers, however long that takes.
    mgr = SoulSessionManager(idle_seconds=600.0)
    warm = await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")
    warm.last_access = time.monotonic() - 100000  # far past the idle window
    mgr.pin("writer/p1")

    mgr._evict_idle()
    assert "writer/p1" in mgr._sessions  # pinned → kept

    mgr.unpin("writer/p1")
    mgr._evict_idle()
    assert mgr._sessions == {}  # unpinned → evicted as usual


async def test_close_all_cancels_reaper_and_clears() -> None:
    mgr = SoulSessionManager()
    await mgr.acquire("writer/p1", app_name=constants.APP_NAME, user_id="u")
    assert mgr._reaper is not None

    reaper = mgr._reaper
    await mgr.close_all()
    await asyncio.sleep(0)  # let the cancellation propagate

    assert mgr._sessions == {}
    assert reaper is not None and reaper.done()

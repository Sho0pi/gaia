"""The self-improve scheduler: run one improve cycle every ``interval_hours`` in the daemon.

Mirrors :class:`gaia.cron.scheduler.CronScheduler` but trivially — a single repeating job
(no store). Singleton execution so a slow cycle never overlaps itself. The cycle callback is
injected so this module knows nothing about agents.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

#: Late fires within this window still run once; older ones are coalesced, not replayed.
_MISFIRE_GRACE_SECONDS = 3600


class AnalysisScheduler:
    """Runs ``cycle`` every ``interval_hours`` inside the daemon's event loop."""

    def __init__(self, cycle: Callable[[], Awaitable[object]], *, interval_hours: float) -> None:
        self._cycle = cycle
        self._interval_hours = max(0.1, interval_hours)
        self._scheduler: AsyncIOScheduler | None = None

    def start(self) -> None:
        """Build the scheduler on the running loop and schedule the recurring cycle."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "max_instances": 1,  # a slow cycle can't overlap itself
                "coalesce": True,
                "misfire_grace_time": _MISFIRE_GRACE_SECONDS,
            }
        )
        self._scheduler.add_job(
            self._fire,
            trigger=IntervalTrigger(hours=self._interval_hours),
            id="improve",
        )
        self._scheduler.start()
        logger.info("self-improve scheduler started (every %sh)", self._interval_hours)

    def shutdown(self) -> None:
        """Stop the scheduler (a running cycle finishes; nothing new starts)."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    async def _fire(self) -> None:
        try:
            await self._cycle()
        except Exception as exc:  # a failing cycle must never kill the scheduler
            from gaia.logs import log_error

            # one call: traceback -> system.log + structured event -> events.jsonl
            log_error("improve_scheduler", exc)

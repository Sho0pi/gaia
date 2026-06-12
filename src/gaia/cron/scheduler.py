"""The cron engine: APScheduler drives the stored jobs inside the daemon.

The god-PR pattern in Python — a proven scheduler library for the timing math, our own
store for persistence. Singleton execution (``max_instances=1`` + ``coalesce``): a slow
"every minute" LLM run can never overlap itself or stampede after a sleep. The runner
callback is injected so this module knows nothing about agents or connectors.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from gaia.cron.store import CronJob, CronStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

#: A fired job's handler: receives the CronJob, runs the turn, delivers replies.
Runner = Callable[[CronJob], Awaitable[None]]

#: Late fires within this window still run once (laptop slept through 09:00 → fire at
#: wake); anything older is dropped by coalescing rather than replayed N times.
MISFIRE_GRACE_SECONDS = 3600


class CronScheduler:
    """Mirrors the :class:`CronStore` onto a running APScheduler."""

    def __init__(self, store: CronStore, runner: Runner) -> None:
        self._store = store
        self._runner = runner
        self._scheduler: AsyncIOScheduler | None = None

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        """Build the scheduler on the running loop and load every enabled job."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "max_instances": 1,  # singleton: a slow run can't overlap itself
                "coalesce": True,  # missed fires collapse into one
                "misfire_grace_time": MISFIRE_GRACE_SECONDS,
            }
        )
        for job in self._store.list():
            if job.enabled:
                self._schedule(job)
        # The store is also written by the cron tool and `gaia cron` while we run;
        # re-sync periodically so their edits go live without a daemon restart.
        from apscheduler.triggers.interval import IntervalTrigger

        self._scheduler.add_job(self._resync, trigger=IntervalTrigger(seconds=30), id="_resync")
        self._scheduler.start()
        logger.info("cron scheduler started (%d job(s))", len(self._scheduler.get_jobs()) - 1)

    def shutdown(self) -> None:
        """Stop the scheduler (running fires finish; nothing new starts)."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    # -- store-mirrored operations -------------------------------------------------

    def add(self, job: CronJob) -> CronJob:
        """Persist + schedule a new job."""
        self._store.add(job)
        if job.enabled and self._scheduler is not None:
            self._schedule(job)
        return job

    def remove(self, job_id: str) -> bool:
        """Delete a job from the store and the live scheduler."""
        removed = self._store.remove(job_id)
        self._unschedule(job_id)
        return removed

    def set_enabled(self, job_id: str, enabled: bool) -> bool:
        """Flip a job's enabled flag, scheduling/unscheduling it live."""
        job = self._store.get(job_id)
        if job is None:
            return False
        job.enabled = enabled
        self._store.update(job)
        self._unschedule(job_id)
        if enabled and self._scheduler is not None:
            self._schedule(job)
        return True

    def update(self, job: CronJob) -> None:
        """Replace a job's definition and reschedule it."""
        self._store.update(job)
        self._unschedule(job.id)
        if job.enabled and self._scheduler is not None:
            self._schedule(job)

    # -- internals -------------------------------------------------------------------

    def _trigger(self, job: CronJob) -> Any:
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.date import DateTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        if job.kind == "cron":
            return CronTrigger.from_crontab(job.expr)
        if job.kind == "every":
            return IntervalTrigger(seconds=int(job.expr))
        return DateTrigger(run_date=datetime.fromisoformat(job.expr))

    def _schedule(self, job: CronJob) -> None:
        assert self._scheduler is not None
        self._scheduler.add_job(
            self._fire, trigger=self._trigger(job), id=job.id, args=[job.id], replace_existing=True
        )

    def _unschedule(self, job_id: str) -> None:
        if self._scheduler is not None and self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)

    async def _resync(self) -> None:
        """Mirror the store onto the live scheduler (tool/CLI edits made elsewhere)."""
        if self._scheduler is None:
            return
        stored = {job.id: job for job in self._store.list()}
        live = {j.id for j in self._scheduler.get_jobs() if j.id != "_resync"}
        for job_id in live - stored.keys():
            self._unschedule(job_id)  # removed externally
        for job in stored.values():
            if job.enabled and job.id not in live:
                self._schedule(job)  # added/enabled externally
            elif not job.enabled and job.id in live:
                self._unschedule(job.id)  # disabled externally

    async def _fire(self, job_id: str) -> None:
        """Run one fire: fresh job read (live edits win), runner, last-run bookkeeping."""
        job = self._store.get(job_id)
        if job is None or not job.enabled:
            self._unschedule(job_id)
            return
        logger.info("cron job %s (%s) firing", job.id, job.name or job.message[:40])
        try:
            await self._runner(job)
        except Exception:
            # A failing turn must never kill the scheduler loop; next fire tries again.
            logger.exception("cron job %s failed", job.id)
        finally:
            self._store.mark_ran(job.id)  # one-shots are deleted here
            if job.delete_after_run:
                self._unschedule(job.id)

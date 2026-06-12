"""CronScheduler over a real APScheduler: boot-load, fire, one-shot removal, re-sync.

'every' jobs have a 30s production floor, so firing is tested with DateTrigger one-shots
a few hundred ms out — real scheduler, fast tests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from gaia.cron import CronJob, CronScheduler, CronStore
from gaia.cron.store import CronJob as Job


def _soon(ms: int = 300) -> str:
    return (datetime.now() + timedelta(milliseconds=ms)).isoformat()


async def test_at_job_fires_once_and_is_removed(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron.json")
    fired: list[str] = []

    async def runner(job: Job) -> None:
        fired.append(job.id)

    scheduler = CronScheduler(store, runner)
    scheduler.start()
    try:
        job = scheduler.add(CronJob(kind="at", expr=_soon(), message="once"))
        await asyncio.sleep(0.8)
    finally:
        scheduler.shutdown()

    assert fired == [job.id]
    assert store.get(job.id) is None  # one-shot removed from the store after firing


async def test_boot_loads_enabled_jobs_only(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron.json")
    on = store.add(CronJob(kind="at", expr=_soon(), message="on"))
    off = store.add(CronJob(kind="at", expr=_soon(), message="off", enabled=False))
    fired: list[str] = []

    async def runner(job: Job) -> None:
        fired.append(job.id)

    scheduler = CronScheduler(store, runner)
    scheduler.start()
    try:
        await asyncio.sleep(0.8)
    finally:
        scheduler.shutdown()

    assert on.id in fired
    assert off.id not in fired


async def test_disable_stops_firing(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron.json")

    async def runner(job: Job) -> None:  # pragma: no cover - must not run
        raise AssertionError("disabled job fired")

    scheduler = CronScheduler(store, runner)
    scheduler.start()
    try:
        job = scheduler.add(CronJob(kind="at", expr=_soon(500), message="x"))
        assert scheduler.set_enabled(job.id, False) is True
        await asyncio.sleep(0.9)
    finally:
        scheduler.shutdown()


async def test_failing_runner_does_not_kill_scheduler(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron.json")
    calls: list[str] = []

    async def runner(job: Job) -> None:
        calls.append(job.id)
        raise RuntimeError("turn failed")

    scheduler = CronScheduler(store, runner)
    scheduler.start()
    try:
        a = scheduler.add(CronJob(kind="at", expr=_soon(200), message="a"))
        b = scheduler.add(CronJob(kind="at", expr=_soon(500), message="b"))
        await asyncio.sleep(1.0)
    finally:
        scheduler.shutdown()

    assert calls == [a.id, b.id]  # the second still fired after the first raised


async def test_resync_picks_up_external_store_edits(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron.json")
    fired: list[str] = []

    async def runner(job: Job) -> None:
        fired.append(job.id)

    scheduler = CronScheduler(store, runner)
    scheduler.start()
    try:
        # Simulate the cron tool / CLI writing the store directly (no scheduler.add).
        external = store.add(CronJob(kind="at", expr=_soon(400), message="external"))
        await scheduler._resync()  # the daemon does this every 30s
        await asyncio.sleep(0.9)
    finally:
        scheduler.shutdown()

    assert fired == [external.id]

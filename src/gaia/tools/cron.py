"""The ``cron`` tool: the LLM schedules its own future work (picoclaw's shape).

ONE tool with an ``action`` enum instead of seven separate tools — scheduling is a
single capability and a single schema keeps the per-request token cost down (issue
#89). ``add`` captures the current chat from the connector contextvar so the result of
a fired job is delivered to whoever asked. The store is shared with ``gaia cron`` and
the daemon's scheduler — jobs added here are picked up on the daemon's next start (or
immediately, when a live scheduler is attached).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gaia.connectors.base import current_chat
from gaia.cron.store import CronJob, CronStore, validate_schedule
from gaia.tools._helpers import err, ok

#: Tool id, used by the registry and as the ADK tool name (matches the closure name).
NAME = "cron"

_ACTIONS = ("add", "list", "get", "update", "remove", "enable", "disable")


def _parse_schedule(schedule: str) -> tuple[str, str]:
    """Map the tool's schedule string onto a store (kind, expr) pair."""
    if schedule.startswith("every:"):
        return "every", schedule.removeprefix("every:")
    if schedule.startswith("at:"):
        return "at", schedule.removeprefix("at:")
    return "cron", schedule


def make_cron(store: CronStore | None = None) -> Callable[..., dict[str, Any]]:
    """Return the ADK cron tool bound to ``store`` (default: the real ~/.gaia store)."""
    store = store or CronStore()

    def cron(
        action: str,
        schedule: str = "",
        message: str = "",
        name: str = "",
        job_id: str = "",
    ) -> dict[str, Any]:
        """Manage scheduled jobs: gaia runs a message later / on a recurring schedule
        and delivers the result to the user's chat.

        Args:
            action: one of add | list | get | update | remove | enable | disable.
            schedule: a 5-field cron expression ('0 9 * * *'), 'every:<seconds>', or
                'at:<ISO datetime>' (one-shot, auto-deleted after it runs).
            message: what to do when the job fires, in plain language.
            name: short human label for the job.
            job_id: target job for get/update/remove/enable/disable.
        """
        try:
            if action == "add":
                if not schedule or not message:
                    return err("add needs both schedule and message")
                kind, expr = _parse_schedule(schedule)
                error = validate_schedule(kind, expr)
                if error:
                    return err(error)
                channel, chat = current_chat.get()
                job = store.add(
                    CronJob(
                        name=name,
                        kind=kind,
                        expr=expr,
                        message=message,
                        channel=channel,
                        chat=chat,
                    )
                )
                return ok(job=job.model_dump())

            if action == "list":
                return ok(jobs=[j.model_dump() for j in store.list()])

            if action in ("get", "update", "remove", "enable", "disable"):
                if not job_id:
                    return err(f"{action} needs job_id")
                job = store.get(job_id)  # type: ignore[assignment]
                if job is None:
                    return err(f"no job {job_id!r}")
                if action == "get":
                    return ok(job=job.model_dump())
                if action == "remove":
                    store.remove(job_id)
                    return ok(removed=job_id)
                if action in ("enable", "disable"):
                    job.enabled = action == "enable"
                    store.update(job)
                    return ok(job=job.model_dump())
                # update: replace the provided fields, keep the rest.
                if schedule:
                    kind, expr = _parse_schedule(schedule)
                    error = validate_schedule(kind, expr)
                    if error:
                        return err(error)
                    job.kind, job.expr = kind, expr
                if message:
                    job.message = message
                if name:
                    job.name = name
                store.update(job)
                return ok(job=job.model_dump())

            return err(f"unknown action {action!r} (use {', '.join(_ACTIONS)})")
        except Exception as exc:  # tools never raise to the model
            return err(str(exc))

    return cron

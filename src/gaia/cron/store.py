"""The cron job store: ``~/.gaia/cron.json``, owned by the cron tool / ``gaia cron``.

A *job* says: at this schedule, run this message as a system-initiated agent turn and
deliver the replies to this chat. Three schedule kinds (picoclaw's trio):

* ``cron``  — a 5-field crontab expression (``0 9 * * *``), exactly cron's semantics;
* ``every`` — a fixed interval in seconds (floor 30s, so a runaway can't hot-loop);
* ``at``    — a one-shot ISO datetime; the job is auto-deleted after it fires.

JSON file (not gaia.yaml) because jobs carry **runtime state** the scheduler rewrites —
``last_run``, one-shot deletion — which doesn't belong in a hand-edited config. Writes
are atomic (tmp + rename), the agent_registry pattern. APScheduler is imported lazily
only to *validate* cron expressions, so this module stays light.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from gaia import constants

#: Minimum interval for ``every`` jobs — a slow LLM turn per fire makes anything
#: tighter a hot-loop (the god PR's 30s floor).
MIN_EVERY_SECONDS = 30


class CronJob(BaseModel):
    """One scheduled job. ``expr`` is interpreted per ``kind`` (crontab / seconds / ISO)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    kind: str  # cron | every | at
    expr: str  # interpreted per `kind`: crontab string | whole seconds | ISO datetime
    message: str
    channel: str = ""  # connector name (telegram/whatsapp); empty = cron.deliver default
    chat: str = ""  # connector-specific chat id (telegram chat / whatsapp JID)
    enabled: bool = True
    delete_after_run: bool = False  # auto-true for 'at' jobs
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_run: str | None = None


def validate_schedule(kind: str, expr: str) -> str | None:
    """Return an error message for an invalid ``kind``/``expr`` pair, or ``None`` if ok."""
    if kind == "cron":
        from apscheduler.triggers.cron import CronTrigger

        try:
            CronTrigger.from_crontab(expr)
        except ValueError as exc:
            return f"invalid cron expression {expr!r}: {exc}"
        return None
    if kind == "every":
        try:
            seconds = int(expr)
        except ValueError:
            return f"'every' needs whole seconds, got {expr!r}"
        if seconds < MIN_EVERY_SECONDS:
            return f"interval too small: {seconds}s (minimum {MIN_EVERY_SECONDS}s)"
        return None
    if kind == "at":
        try:
            when = datetime.fromisoformat(expr)
        except ValueError:
            return f"'at' needs an ISO datetime, got {expr!r}"
        if when <= datetime.now():
            return f"'at' time is in the past: {expr}"
        return None
    return f"unknown schedule kind {kind!r} (cron/every/at)"


#: Annotation alias: inside CronStore the name `list` is the method, not the builtin.
JobList = list[CronJob]


class CronStore:
    """File-backed job store; one JSON array, atomically rewritten on every change."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else constants.CRON_FILE

    def list(self) -> JobList:
        """Every stored job, file order (empty list when the file is missing)."""
        if not self._path.exists():
            return []
        raw = json.loads(self._path.read_text() or "[]")
        return [CronJob.model_validate(item) for item in raw]

    def get(self, job_id: str) -> CronJob | None:
        """The job with ``job_id``, or ``None``."""
        return next((job for job in self.list() if job.id == job_id), None)

    def add(self, job: CronJob) -> CronJob:
        """Validate and append ``job``; returns it (with its generated id)."""
        error = validate_schedule(job.kind, job.expr)
        if error:
            raise ValueError(error)
        if job.kind == "at":
            job.delete_after_run = True
        self._write([*self.list(), job])
        return job

    def update(self, job: CronJob) -> None:
        """Replace the stored job with the same id (validates the schedule)."""
        error = validate_schedule(job.kind, job.expr)
        if error:
            raise ValueError(error)
        jobs = [job if existing.id == job.id else existing for existing in self.list()]
        self._write(jobs)

    def remove(self, job_id: str) -> bool:
        """Delete a job by id. True if it existed."""
        jobs = self.list()
        kept = [job for job in jobs if job.id != job_id]
        if len(kept) == len(jobs):
            return False
        self._write(kept)
        return True

    def mark_ran(self, job_id: str) -> None:
        """Record a fire: set ``last_run``; one-shots (``delete_after_run``) are removed."""
        jobs = self.list()
        for job in jobs:
            if job.id == job_id:
                job.last_run = datetime.now().isoformat(timespec="seconds")
        # One-shots are dropped after their single run; everything else is kept.
        self._write([job for job in jobs if not (job.id == job_id and job.delete_after_run)])

    def _write(self, jobs: JobList) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([job.model_dump() for job in jobs], indent=2) + "\n")
        os.replace(tmp, self._path)  # atomic on POSIX

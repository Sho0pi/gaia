"""``gaia cron`` command group — crontab parity for gaia's scheduled jobs.

Direct CRUD over the same ``~/.gaia/cron.json`` the LLM tool and the daemon scheduler
use; a running daemon picks up edits within its 30s re-sync. ``edit`` opens the file in
``$EDITOR`` with validate-on-save — the ``crontab -e`` experience.

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from typing import Annotated

import click
import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

# Argument/option types named once so the command signatures below stay readable.
JobIdArg = Annotated[str, typer.Argument(help="Job id.")]
ExprArg = Annotated[
    str, typer.Argument(help="Cron expression ('0 9 * * *'); or use --every/--at instead.")
]
MessageArg = Annotated[str, typer.Argument(help="What gaia should do when it fires.")]
EveryOpt = Annotated[int, typer.Option("--every", help="Interval in seconds (min 30).")]
AtOpt = Annotated[str, typer.Option("--at", help="One-shot ISO datetime (auto-deletes).")]
NameOpt = Annotated[str, typer.Option("--name", help="Short label.")]
ChannelOpt = Annotated[
    str, typer.Option("--channel", help="Deliver to this connector (telegram/whatsapp).")
]
ChatOpt = Annotated[str, typer.Option("--chat", help="Chat id on that connector.")]

app = typer.Typer(name="cron", help="Schedule jobs gaia runs on its own.", no_args_is_help=True)


@app.command("list")
def list_jobs(ctx: typer.Context) -> None:
    """List every scheduled job."""
    from gaia.cron import CronStore

    jobs = CronStore().list()
    if state(ctx).json:
        emit_json({"jobs": [j.model_dump() for j in jobs]})
        return
    out = console()
    if not jobs:
        out.print("no jobs — add one with 'gaia cron add' (or ask gaia in chat)")
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    for col in ("id", "name", "schedule", "message", "to", "on", "last run"):
        table.add_column(col)
    for j in jobs:
        schedule = j.expr if j.kind == "cron" else f"{j.kind}:{j.expr}"
        to = f"{j.channel}:{j.chat}" if j.channel else "(default)"
        table.add_row(
            j.id, j.name, schedule, j.message[:40], to, "✓" if j.enabled else "✗", j.last_run or "-"
        )
    out.print(table)


@app.command()
def show(ctx: typer.Context, job_id: JobIdArg) -> None:
    """Print one job in full (raw JSON with --json)."""
    from gaia.cron import CronStore

    job = CronStore().get(job_id)
    if job is None:
        console().print(f"no job {job_id!r}")
        raise typer.Exit(1)
    if state(ctx).json:
        emit_json(job.model_dump())
    else:
        console().print(job.model_dump())


@app.command("new")
def add(
    ctx: typer.Context,
    expr: ExprArg = "",
    message: MessageArg = "",
    every: EveryOpt = 0,
    at: AtOpt = "",
    name: NameOpt = "",
    channel: ChannelOpt = "",
    chat: ChatOpt = "",
) -> None:
    """Add a job: EXPR MESSAGE, or --every N / --at WHEN with MESSAGE."""
    from gaia.cron import CronJob, CronStore

    if every:
        kind, expression, message = "every", str(every), message or expr
    elif at:
        kind, expression, message = "at", at, message or expr
    else:
        kind, expression = "cron", expr
    if not expression or not message:
        console().print("need a schedule and a message — e.g. gaia cron add '0 9 * * *' 'news'")
        raise typer.Exit(2)

    try:
        job = CronStore().add(
            CronJob(
                name=name, kind=kind, expr=expression, message=message, channel=channel, chat=chat
            )
        )
    except ValueError as exc:
        console().print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    console().print(f"added job {job.id} — live within 30s while the daemon runs")


@app.command()
def rm(ctx: typer.Context, job_id: JobIdArg) -> None:
    """Delete a job."""
    from gaia.cron import CronStore

    if not CronStore().remove(job_id):
        console().print(f"no job {job_id!r}")
        raise typer.Exit(1)
    console().print(f"removed {job_id}")


# `remove` stays as a hidden alias for `rm` (back-compat).
app.command("remove", hidden=True)(rm)


@app.command()
def enable(ctx: typer.Context, job_id: JobIdArg) -> None:
    """Enable a disabled job."""
    _flip(job_id, True)


@app.command()
def disable(ctx: typer.Context, job_id: JobIdArg) -> None:
    """Disable a job without deleting it."""
    _flip(job_id, False)


def _flip(job_id: str, enabled: bool) -> None:
    from gaia.cron import CronStore

    store = CronStore()
    job = store.get(job_id)
    if job is None:
        console().print(f"no job {job_id!r}")
        raise typer.Exit(1)
    job.enabled = enabled
    store.update(job)
    console().print(f"{'enabled' if enabled else 'disabled'} {job_id}")


@app.command()
def edit(ctx: typer.Context) -> None:
    """Open the whole job file in $EDITOR (crontab -e style); validates on save."""
    import json as jsonlib

    from gaia import constants
    from gaia.cron import CronJob, validate_schedule

    current = constants.CRON_FILE.read_text() if constants.CRON_FILE.exists() else "[]\n"
    edited = click.edit(current, extension=".json")
    if edited is None:
        console().print("no changes")
        return
    try:
        jobs = [CronJob.model_validate(item) for item in jsonlib.loads(edited)]
        for job in jobs:
            error = validate_schedule(job.kind, job.expr)
            if error:
                raise ValueError(f"job {job.id}: {error}")
    except (ValueError, TypeError) as exc:
        console().print(f"[red]invalid — not saved:[/] {exc}")
        raise typer.Exit(1) from exc
    constants.CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
    constants.CRON_FILE.write_text(jsonlib.dumps([j.model_dump() for j in jobs], indent=2) + "\n")
    console().print(f"saved {len(jobs)} job(s)")

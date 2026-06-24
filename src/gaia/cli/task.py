"""``gaia task`` command group — inspect the missions task board from the terminal.

Read-only over the same ``~/.gaia/tasks.db`` the ``task_*`` tools and (later) the dispatcher
use; WAL means a `list` here is safe while a chat turn writes. The local operator owns the
machine, so the CLI sees every owner by default (``--user`` narrows). Per-user scoping lives
on the chat side (the `/task` command), not here.

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from typing import Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

app = typer.Typer(name="task", help="Inspect the missions task board.", no_args_is_help=True)

# Argument/option types named once so the command signatures below stay readable.
MissionOpt = Annotated[str, typer.Option("--mission", help="Only this mission's tasks.")]
StatusOpt = Annotated[str, typer.Option("--status", help="Only tasks in this status.")]
UserOpt = Annotated[str, typer.Option("--user", help="Only this owner's tasks.")]
TaskIdArg = Annotated[str, typer.Argument(help="Task id.")]


@app.command("list")
def list_tasks(
    ctx: typer.Context,
    mission: MissionOpt = "",
    status: StatusOpt = "",
    user: UserOpt = "",
) -> None:
    """List tasks on the board (newest first)."""
    from gaia.missions import TaskStatus, TaskStore

    parsed: TaskStatus | None = None
    if status:
        try:
            parsed = TaskStatus(status)
        except ValueError as exc:
            console().print(f"[red]unknown status {status!r}[/]")
            raise typer.Exit(2) from exc

    tasks = TaskStore().list(mission=mission or None, status=parsed, owner=user or None)
    if state(ctx).json:
        emit_json({"tasks": [t.model_dump(mode="json") for t in tasks]})
        return
    out = console()
    if not tasks:
        out.print("no tasks — gaia files them when you ask it to track work")
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    for col in ("id", "mission", "title", "status", "owner", "blocked by", "updated"):
        table.add_column(col)
    for t in tasks:
        table.add_row(
            t.id,
            t.mission_id,
            t.title[:40],
            t.status.value,
            t.owner or "-",
            ",".join(t.blocked_by) or "-",
            t.updated_at,
        )
    out.print(table)


@app.command()
def show(ctx: typer.Context, task_id: TaskIdArg) -> None:
    """Print one task in full (raw JSON with --json)."""
    from gaia.missions import TaskStore

    task = TaskStore().get(task_id)
    if task is None:
        console().print(f"no task {task_id!r}")
        raise typer.Exit(1)
    if state(ctx).json:
        emit_json(task.model_dump(mode="json"))
    else:
        console().print(task.model_dump(mode="json"))

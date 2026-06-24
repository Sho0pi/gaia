"""``gaia memory`` command group — inspect & clear long-term memory from the terminal.

Memory is per-user (mem0 partitions by ``user_id``), so every command takes a user. Builds a real
``Gaia`` to reach the same ``memory_service`` the chat ``/memory``/``/forget`` use; needs
``memory.enabled`` + a model key, and skips cleanly otherwise. (No ``search`` subcommand —
``gaia memory list <user> | grep …`` covers it.)

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

app = typer.Typer(name="memory", help="Inspect and clear long-term memory.", no_args_is_help=True)

UserArg = Annotated[
    str, typer.Argument(help="The user id whose memory to act on (the mem0 partition key).")
]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")]


def _with_memory(ctx: typer.Context, fn: Callable[[Any], Awaitable[None]]) -> None:
    """Build a Gaia, hand its memory_service to ``fn``, and close it — or exit if memory is off."""
    import asyncio

    from gaia.config import get_settings
    from gaia.core import Gaia

    async def run() -> None:
        gaia = Gaia(get_settings(state(ctx).env_file))
        try:
            service = gaia.memory_service
            if service is None:
                console().print("long-term memory is off (set memory.enabled + a model key)")
                raise typer.Exit(1)
            await fn(service)
        finally:
            await gaia.close()

    asyncio.run(run())


@app.command("list")
def list_memories(ctx: typer.Context, user: UserArg) -> None:
    """List what gaia remembers about a user long-term."""

    async def show(service: Any) -> None:
        items = await service.list_memories(user_id=user)
        if state(ctx).json:
            emit_json({"user": user, "memories": items})
            return
        out = console()
        if not items:
            out.print(f"nothing remembered about {user!r} yet")
            return
        for item in items:
            out.print(f"- {item}")

    _with_memory(ctx, show)


@app.command()
def forget(ctx: typer.Context, user: UserArg, yes: YesOpt = False) -> None:
    """Wipe a user's long-term memory (destructive)."""

    async def wipe(service: Any) -> None:
        items = await service.list_memories(user_id=user)
        if not items:
            console().print(f"nothing remembered about {user!r} — nothing to forget")
            return
        if not yes:
            typer.confirm(
                f"permanently wipe all {len(items)} memory item(s) for {user!r}?", abort=True
            )
        removed = await service.forget(user_id=user)
        console().print(f"forgot everything for {user} ({removed} item(s))")

    _with_memory(ctx, wipe)

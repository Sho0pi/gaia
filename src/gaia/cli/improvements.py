"""``gaia improvements`` — inspect and revert what the self-improve loop changed.

Reads the journal (``~/.gaia/improvements.jsonl``) and can undo one change (remove an added
skill/soul, restore a refined soul from its ``.bak``). Offline — no model key.
"""

from __future__ import annotations

from typing import Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

app = typer.Typer(
    name="improvements", help="Inspect and revert gaia's self-improvements.", no_args_is_help=True
)


@app.command("list")
def list_improvements(ctx: typer.Context) -> None:
    """List every change the self-improve loop applied (newest last)."""
    from gaia.analysis.journal import ImprovementJournal

    entries = ImprovementJournal().entries()
    if state(ctx).json:
        emit_json({"improvements": [vars(e) for e in entries]})
        return
    out = console()
    if not entries:
        out.print("no self-improvements recorded yet")
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    for col in ("id", "type", "action", "target", "reverted"):
        table.add_column(col)
    for e in entries:
        table.add_row(e.id, e.type, e.action, e.target, "yes" if e.reverted else "")
    out.print(table)


@app.command()
def run(ctx: typer.Context) -> None:
    """Run one self-improve cycle now (analyze recent usage and apply). Needs a model key."""
    import asyncio

    from gaia.analysis.loop import run_cycle
    from gaia.config import get_settings
    from gaia.core import Gaia

    out = console()

    async def _go() -> None:
        gaia = Gaia(get_settings(state(ctx).env_file))
        try:
            applied = await run_cycle(gaia)
        finally:
            await gaia.close()
        if not applied:
            out.print("no improvements this cycle (nothing worth changing — that's fine)")
            return
        for imp in applied:
            out.print(f"- {imp.action} {imp.type}: {imp.target}  ({imp.id})")
        out.print(f"\napplied {len(applied)} improvement(s); see 'gaia improvements list'")

    asyncio.run(_go())


@app.command()
def revert(
    ctx: typer.Context,
    improvement_id: Annotated[str, typer.Argument(help="The improvement id (from 'list').")],
) -> None:
    """Undo one improvement by id (removes an added skill/soul, restores a refined soul)."""
    from gaia.agents import SoulRegistry
    from gaia.analysis.apply import revert_improvement
    from gaia.config import ConfigSupplier, get_settings
    from gaia.skills import resolve_skills_dir

    settings = get_settings(state(ctx).env_file)
    cfg = ConfigSupplier(settings.config_path).current
    msg = revert_improvement(
        improvement_id,
        skills_dir=resolve_skills_dir(cfg),
        registry=SoulRegistry(settings.agent_registry_dir),
    )
    console().print(msg)

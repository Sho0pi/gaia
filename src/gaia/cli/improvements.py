"""``gaia improvements`` — inspect and revert what the self-improve loop changed.

Reads the ``~/.gaia`` git history of skill/soul changes (:mod:`gaia.state`) and can
``git revert`` one change. Offline — no model key (only ``run`` needs one).
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
    """List the skill/soul changes in git history (newest first)."""
    from gaia.state import StateRepo

    entries = StateRepo().entries()
    if state(ctx).json:
        emit_json({"history": [vars(e) for e in entries]})
        return
    out = console()
    if not entries:
        out.print("no changes recorded yet")
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    table.add_column("commit")
    table.add_column("change")
    table.add_column("reverted")
    for e in entries:
        table.add_row(e.sha, e.subject, "yes" if e.reverted else "")
    out.print(table)
    out.print("\nfull detail + diff: 'gaia improvements show <commit>'")


@app.command()
def show(
    ctx: typer.Context,
    commit: Annotated[str, typer.Argument(help="The commit sha (from 'list').")],
) -> None:
    """Show a change's full commit message + diff."""
    from gaia.state import StateRepo

    console().print(StateRepo().show(commit))


@app.command()
def run(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Analyze and print proposals without applying.")
    ] = False,
) -> None:
    """Run one self-improve cycle now (analyze recent usage and apply). Needs a model key."""
    import asyncio

    from gaia.analysis.loop import analyze, run_cycle
    from gaia.config import get_settings
    from gaia.core import Gaia

    out = console()

    async def _go() -> None:
        gaia = Gaia(get_settings(state(ctx).env_file))
        try:
            if dry_run:
                report, _ = await analyze(gaia)
                if report is None:
                    out.print("nothing to analyze (no recent events / no model)")
                    return
                out.print(f"[bold]{report.summary}[/]\n")
                for s in report.skills:
                    out.print(f"[cyan]skill[/] {s.name} — {s.description}\n  why: {s.rationale}")
                for so in report.souls:
                    out.print(f"[cyan]soul ({so.action})[/] {so.name or so.key} — {so.description}")
                for m in report.memories:
                    out.print(f"[cyan]memory[/] ({m.user_id or '?'}) {m.fact}")
                if not (report.skills or report.souls or report.memories):
                    out.print("(no proposals — nothing worth changing)")
                out.print("\n[dim]dry run — nothing applied[/]")
                return
            applied = await run_cycle(gaia)
            if not applied:
                out.print("no improvements this cycle (nothing worth changing — that's fine)")
                return
            for line in applied:
                out.print(f"- {line}")
            out.print(f"\napplied {len(applied)} change(s); see 'gaia improvements list'")
        finally:
            await gaia.close()

    asyncio.run(_go())


@app.command()
def revert(
    ctx: typer.Context,
    commit: Annotated[str, typer.Argument(help="The commit sha to revert (from 'list').")],
) -> None:
    """Revert one change by commit sha (a new commit undoing it)."""
    from gaia.state import StateRepo

    console().print(StateRepo().revert(commit))

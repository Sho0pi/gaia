"""``gaia grow`` — run the self-improve loop and inspect/revert what it changed.

``run`` mines recent usage into proposals and applies them (autonomously, or with
``-i`` after you approve each). ``list`` / ``show`` / ``revert`` read the ``~/.gaia``
git history of skill/soul changes (:mod:`gaia.state`). Offline except ``run`` (model key).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from rich.console import Console

    from gaia.analysis import AnalysisReport

app = typer.Typer(name="grow", help="Run and review gaia's self-improvement.", no_args_is_help=True)

# Argument/option types named once so the command signatures below stay readable.
CommitArg = Annotated[str, typer.Argument(help="The commit sha (from 'list').")]
RevertCommitArg = Annotated[str, typer.Argument(help="The commit sha to revert (from 'list').")]
DryRunOpt = Annotated[
    bool, typer.Option("--dry-run", help="Analyze and print proposals without applying.")
]
InteractiveOpt = Annotated[
    bool, typer.Option("-i", "--interactive", help="Approve each proposal before applying.")
]


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
    out.print("\nfull detail + diff: 'gaia grow show <commit>'")


@app.command()
def show(
    ctx: typer.Context,
    commit: CommitArg,
) -> None:
    """Show a change's full commit message + diff."""
    from gaia.state import StateRepo

    console().print(StateRepo().show(commit))


@app.command()
def run(
    ctx: typer.Context,
    dry_run: DryRunOpt = False,
    interactive: InteractiveOpt = False,
) -> None:
    """Run one self-improve cycle now. Default applies autonomously; ``--dry-run`` previews;
    ``-i`` approves each proposal before applying. Needs a model key."""
    import asyncio

    from gaia.analysis.apply import apply_report
    from gaia.analysis.loop import analyze, run_cycle
    from gaia.config import get_settings
    from gaia.core import Gaia

    out = console()

    async def _go() -> None:
        gaia = Gaia(get_settings(state(ctx).env_file))
        try:
            if not dry_run and not interactive:  # autonomous: analyze + apply in one shot
                _print_applied(out, await run_cycle(gaia))
                return

            report, single_user = await analyze(gaia)
            if report is None:
                out.print("nothing to analyze (no recent events / no model)")
                return
            out.print(f"[bold]{report.summary}[/]\n")

            if dry_run:
                _print_report(out, report)
                out.print("\n[dim]dry run — nothing applied[/]")
                return

            # interactive: confirm each proposal, then apply only the approved ones.
            trimmed = report.model_copy(
                update={
                    "skills": [
                        s
                        for s in report.skills
                        if typer.confirm(f"create skill '{s.name}' — {s.description}?")
                    ],
                    "souls": [
                        so
                        for so in report.souls
                        if typer.confirm(
                            f"{so.action} soul '{so.name or so.key}' — {so.description}?"
                        )
                    ],
                    "memories": [
                        m
                        for m in report.memories
                        if typer.confirm(f"add memory ({m.user_id or '?'}): {m.fact}?")
                    ],
                }
            )
            if not (trimmed.skills or trimmed.souls or trimmed.memories):
                out.print("nothing approved — nothing applied")
                return
            _print_applied(out, await apply_report(gaia, trimmed, user_id=single_user))
        finally:
            await gaia.close()

    asyncio.run(_go())


def _print_report(out: Console, report: AnalysisReport) -> None:
    """Print an analyst report's proposals (the ``--dry-run`` / pre-approval view)."""
    for s in report.skills:
        out.print(f"[cyan]skill[/] {s.name} — {s.description}\n  why: {s.rationale}")
    for so in report.souls:
        out.print(f"[cyan]soul ({so.action})[/] {so.name or so.key} — {so.description}")
    for m in report.memories:
        out.print(f"[cyan]memory[/] ({m.user_id or '?'}) {m.fact}")
    if not (report.skills or report.souls or report.memories):
        out.print("(no proposals — nothing worth changing)")


def _print_applied(out: Console, applied: list[str]) -> None:
    """Print the changes a cycle applied (or the no-op note)."""
    if not applied:
        out.print("no improvements this cycle (nothing worth changing — that's fine)")
        return
    for line in applied:
        out.print(f"- {line}")
    out.print(f"\napplied {len(applied)} change(s); see 'gaia grow list'")


@app.command()
def revert(
    ctx: typer.Context,
    commit: RevertCommitArg,
) -> None:
    """Revert one change by commit sha (a new commit undoing it)."""
    from gaia.state import StateRepo

    console().print(StateRepo().revert(commit))

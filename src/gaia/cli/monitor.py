"""``gaia monitor run`` — run the self-monitoring loop now (read errors, judge, report).

``run`` digests recent error logs, has the health analyst triage them, and reports new findings
(DMs the admin); ``--dry-run`` previews without reporting. Needs a model key.
"""

from __future__ import annotations

from typing import Annotated

import typer

from gaia.cli._console import console
from gaia.cli._options import state

app = typer.Typer(
    name="monitor", help="Run gaia's self-monitoring (error-log triage).", no_args_is_help=True
)

DryRunOpt = Annotated[
    bool, typer.Option("--dry-run", help="Analyze and print findings without reporting.")
]


@app.command()
def run(ctx: typer.Context, dry_run: DryRunOpt = False) -> None:
    """Run one monitor cycle now. Default reports new findings (DM admin); ``--dry-run`` previews.
    Needs a model key."""
    import asyncio

    from gaia.config import get_settings
    from gaia.core import Gaia
    from gaia.monitor.loop import analyze, run_cycle

    out = console()

    async def _go() -> None:
        gaia = Gaia(get_settings(state(ctx).env_file))
        try:
            if dry_run:
                report = await analyze(gaia)
                if report is None:
                    out.print("no errors in the window (nothing to report)")
                    return
                out.print(f"[bold]{report.summary}[/]\n")
                for f in report.findings:
                    out.print(f"\\[{f.severity}] {f.action}: {f.title}")
                    if f.summary:
                        out.print(f"  [dim]{f.summary}[/]")
                out.print("\n[dim]dry run — nothing reported[/]")
                return
            reported = await run_cycle(gaia)
            if not reported:
                out.print("healthy — no new findings to report")
                return
            out.print(f"reported {len(reported)} finding(s):")
            for f in reported:
                out.print(f"- \\[{f.severity}] {f.title}")
        finally:
            await gaia.close()

    asyncio.run(_go())

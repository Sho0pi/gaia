"""The ``gaia`` console command (Typer). Composes the tree; ``main`` is the entry point.

Lazy-import rule (repo convention): cli modules import only typer + stdlib (+ cli
siblings) at module level, so ``gaia --help`` never imports ADK or the connectors.
"""

from __future__ import annotations

from gaia.cli import daemon, doctor, llm, logs, root, soul

# The full command tree, composed explicitly in one place.
app = root.app
app.add_typer(llm.app, name="llm")
app.add_typer(soul.app, name="soul")
app.command()(daemon.serve)
app.command()(daemon.start)
app.command()(daemon.stop)
app.command()(daemon.restart)
app.command()(daemon.status)
app.command()(logs.logs)
app.command()(doctor.doctor)

__all__ = ["app", "main"]


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (``gaia``). ``argv=None`` reads ``sys.argv``."""
    app(args=argv)

"""The ``godpy`` console command (Typer). Composes the tree; ``main`` is the entry point.

Lazy-import rule (repo convention): cli modules import only typer + stdlib (+ cli
siblings) at module level, so ``godpy --help`` never imports ADK or the connectors.
"""

from __future__ import annotations

from godpy.cli import daemon, llm, root

# The full command tree, composed explicitly in one place.
app = root.app
app.add_typer(llm.app, name="llm")
app.command()(daemon.serve)
app.command()(daemon.start)
app.command()(daemon.stop)
app.command()(daemon.restart)
app.command()(daemon.status)

__all__ = ["app", "main"]


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (``godpy``). ``argv=None`` reads ``sys.argv``."""
    app(args=argv)

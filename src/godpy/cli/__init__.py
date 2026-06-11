"""The ``godpy`` console command (Typer). Composes the tree; ``main`` is the entry point.

Lazy-import rule (repo convention): cli modules import only typer + stdlib (+ cli
siblings) at module level, so ``godpy --help`` never imports ADK or the connectors.
"""

from __future__ import annotations

from godpy.cli import daemon, llm, root

app = root.app
app.add_typer(llm.app, name="llm")
daemon.register(app)

__all__ = ["app", "main"]


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point (``godpy``). ``argv=None`` reads ``sys.argv``."""
    app(args=argv)

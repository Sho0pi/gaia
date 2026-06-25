"""The ``gaia`` console command (Typer). Composes the tree; ``main`` is the entry point.

Lazy-import rule (repo convention): cli modules import only typer + stdlib (+ cli
siblings) at module level, so ``gaia --help`` never imports ADK or the connectors.
"""

from __future__ import annotations

from gaia.cli import (
    acl,
    config,
    connect,
    cron,
    daemon,
    doctor,
    grow,
    llm,
    logs,
    memory,
    monitor,
    root,
    setup,
    skill,
    soul,
    task,
    user,
)

# The full command tree, composed explicitly in one place.
app = root.app
app.add_typer(cron.app, name="cron")
app.add_typer(llm.app, name="llm")
app.add_typer(grow.app, name="grow")
app.add_typer(monitor.app, name="monitor")
app.add_typer(setup.app, name="setup")
app.add_typer(skill.app, name="skill")
app.add_typer(soul.app, name="soul")
app.add_typer(task.app, name="task")
app.add_typer(user.app, name="user")
app.add_typer(config.app, name="config")
app.add_typer(acl.app, name="acl")
app.add_typer(memory.app, name="memory")
app.command()(connect.connect)
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

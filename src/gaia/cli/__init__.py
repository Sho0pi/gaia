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
    lifecycle,
    logs,
    memory,
    monitor,
    report,
    root,
    service,
    setup,
    skill,
    soul,
    style,
    task,
    tools,
    user,
)
from gaia.cli._help_theme import apply_help_theme

apply_help_theme()  # paint --help / errors in the gaia palette

# The full command tree, composed explicitly in one place.
app = root.app
app.add_typer(cron.app, name="cron")
app.add_typer(grow.app, name="grow")
app.add_typer(monitor.app, name="monitor")
app.add_typer(setup.app, name="setup")
app.add_typer(service.app, name="service")
app.add_typer(skill.app, name="skill")
app.add_typer(soul.app, name="soul")
app.add_typer(task.app, name="task")
app.add_typer(user.app, name="user")
app.add_typer(config.app, name="config")
app.add_typer(acl.app, name="acl")
app.add_typer(memory.app, name="memory")
app.command(name="model")(setup.model)  # dedicated provider/auth/model picker (was `setup model`)
app.command(name="tools")(tools.tools)  # configure browser / web_search / MCP (+ --all toggles)
app.command(name="style")(style.style)  # show / set Gaia's communication style (voice)
app.command(name="report")(report.report)  # bundle a crash + logs into a GitHub bug report
app.command(name="update")(lifecycle.update)  # upgrade gaia in place (uv pip install from git)
app.command(name="uninstall")(lifecycle.uninstall)  # remove gaia (asks before deleting ~/.gaia)
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

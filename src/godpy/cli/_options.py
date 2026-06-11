"""Shared CLI state: the root callback's global flags, stored on ``ctx.obj``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer


@dataclass(slots=True)
class CliState:
    """Global flags parsed by the root callback, consumed by subcommands."""

    env_file: Path | None = None
    json: bool = False
    no_color: bool = False


def state(ctx: typer.Context) -> CliState:
    """The :class:`CliState` for this invocation (defaults if the callback never ran)."""
    obj = ctx.obj
    return obj if isinstance(obj, CliState) else CliState()

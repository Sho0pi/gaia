"""Core types for in-chat slash commands.

A command is a small coroutine that runs *instead of* the LLM when a message starts
with ``/``. It receives a :class:`CommandContext` (the parsed args plus the live God and
handler) and returns the reply text; the handler sends it. Commands never reach the model
or the memory ingest path — they are pure control surface.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.commands.registry import CommandRegistry
    from godpy.god.agent import God
    from godpy.god.handler import GodHandler

#: The command prefix every command starts with.
PREFIX = "/"


@dataclass
class CommandContext:
    """Everything a command needs: the parsed args plus live runtime handles."""

    args: str
    god: God
    handler: GodHandler
    registry: CommandRegistry
    user_id: str
    session_id: str


#: A command body: takes a context, returns the reply text to send back.
CommandFn = Callable[["CommandContext"], Awaitable[str]]


@dataclass(frozen=True)
class Command:
    """A registered slash command: its id, help text, body, and aliases."""

    name: str
    summary: str
    run: CommandFn
    aliases: tuple[str, ...] = ()
    usage: str = ""

    def help_line(self) -> str:
        """One ``/name [usage] — summary (aka /alias)`` line for ``/help``."""
        head = f"{PREFIX}{self.name}"
        if self.usage:
            head += f" {self.usage}"
        line = f"{head} — {self.summary}"
        if self.aliases:
            line += " (aka " + ", ".join(f"{PREFIX}{a}" for a in self.aliases) + ")"
        return line


def parse(text: str) -> tuple[str, str] | None:
    """Split a raw message into ``(command_name, args)`` if it's a command.

    Returns ``None`` when ``text`` is not a command (no leading ``/``). The name is
    lower-cased and stripped of its prefix; ``args`` is whatever follows, trimmed.
    """
    stripped = text.strip()
    if not stripped.startswith(PREFIX):
        return None
    body = stripped[len(PREFIX) :]
    name, _, args = body.partition(" ")
    return name.lower(), args.strip()


__all__ = ["PREFIX", "Command", "CommandContext", "CommandFn", "parse"]

"""Core types for in-chat slash commands.

A command is a small coroutine that runs *instead of* the LLM when a message starts
with ``/``. It receives a :class:`CommandContext` (the parsed args plus the live Gaia and
handler) and returns the reply text; the handler sends it. Commands never reach the model
or the memory ingest path — they are pure control surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.commands.registry import CommandRegistry
    from gaia.core.agent import Gaia
    from gaia.core.handler import GaiaHandler

#: The command prefix every command starts with.
PREFIX = "/"


@dataclass
class CommandContext:
    """Everything a command needs: the parsed args plus live runtime handles."""

    args: str
    gaia: Gaia
    handler: GaiaHandler
    registry: CommandRegistry
    user_id: str
    session_id: str
    role: str = "admin"  # the caller's role; admin-only commands gate on it


class Command(ABC):
    """A slash command: its metadata as class attributes + a ``run`` method.

    Each command is a subclass setting ``name``/``summary`` (and optional ``aliases`` /
    ``usage``) and implementing :meth:`run`. The registry holds one instance per command.
    """

    name: ClassVar[str]
    summary: ClassVar[str]
    aliases: ClassVar[tuple[str, ...]] = ()
    usage: ClassVar[str] = ""

    @abstractmethod
    async def run(self, ctx: CommandContext) -> str:
        """Execute the command and return the reply text to send back."""

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


__all__ = ["PREFIX", "Command", "CommandContext", "parse"]

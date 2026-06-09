"""In-memory registry of slash commands, mirroring :mod:`godpy.tools.registry`.

Commands are *code*, not data, so the registry is a plain name → :class:`Command` map
populated once by :func:`default_registry`. Each built-in command is on by default and
gated only by ``commands.<name>.enabled: false`` in ``god.yaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from godpy.commands.agents import AgentsCommand
from godpy.commands.base import Command
from godpy.commands.forget import ForgetCommand
from godpy.commands.help import HelpCommand
from godpy.commands.memories import MemoriesCommand
from godpy.commands.remember import RememberCommand
from godpy.commands.reset import ResetCommand
from godpy.commands.status import StatusCommand
from godpy.commands.whoami import WhoamiCommand

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.config import GodConfig

#: One instance of every built-in command.
_BUILTINS: tuple[Command, ...] = (
    HelpCommand(),
    ResetCommand(),
    WhoamiCommand(),
    AgentsCommand(),
    StatusCommand(),
    RememberCommand(),
    MemoriesCommand(),
    ForgetCommand(),
)


class CommandRegistry:
    """Name (and alias) → :class:`Command` map."""

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        """Add ``command`` under its name and every alias (later wins on a clash)."""
        for key in (command.name, *command.aliases):
            self._by_name[key] = command

    def get(self, name: str) -> Command | None:
        """Return the command for ``name`` (or alias), or None if unknown."""
        return self._by_name.get(name.lower())

    def all(self) -> list[Command]:
        """Every distinct registered command, in name order."""
        seen = {cmd.name: cmd for cmd in self._by_name.values()}
        return [seen[name] for name in sorted(seen)]

    def names(self) -> list[str]:
        """Every registered name + alias, sorted."""
        return sorted(self._by_name)


def _is_enabled(config: GodConfig | None, name: str) -> bool:
    """A command is on unless ``god.yaml`` lists it with ``enabled: false``."""
    if config is None:
        return True
    entry = config.commands.get(name)
    return True if entry is None else entry.enabled


def default_registry(config: GodConfig | None = None) -> CommandRegistry:
    """Build the registry with every built-in command enabled by ``config``."""
    registry = CommandRegistry()
    for command in _BUILTINS:
        if _is_enabled(config, command.name):
            registry.register(command)
    return registry

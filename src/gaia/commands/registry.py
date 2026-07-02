"""In-memory registry of slash commands, mirroring :mod:`gaia.tools.registry`.

Commands are *code*, not data, so the registry is a plain name → :class:`Command` map
populated once by :func:`default_registry`. Each built-in command is on by default and
gated only by ``commands.<name>.enabled: false`` in ``gaia.yaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gaia.commands.base import Command
from gaia.commands.effort import EffortCommand
from gaia.commands.forget import ForgetCommand
from gaia.commands.grow import GrowCommand
from gaia.commands.help import HelpCommand
from gaia.commands.mcp import MCPCommand
from gaia.commands.memory import MemoryCommand
from gaia.commands.model import ModelCommand
from gaia.commands.permissions import (
    AclCommand,
    GrantCommand,
    PermsCommand,
    RevokeCommand,
)
from gaia.commands.remember import RememberCommand
from gaia.commands.reset import ResetCommand
from gaia.commands.skill import SkillCommand
from gaia.commands.soul import SoulCommand
from gaia.commands.status import StatusCommand
from gaia.commands.style import StyleCommand
from gaia.commands.task import TaskCommand
from gaia.commands.user import (
    ApproveCommand,
    LinkCommand,
    NameCommand,
    RemoveCommand,
    UserCommand,
)
from gaia.commands.whoami import WhoamiCommand

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import GaiaConfig

#: One instance of every built-in command.
_BUILTINS: tuple[Command, ...] = (
    HelpCommand(),
    ResetCommand(),
    WhoamiCommand(),
    SoulCommand(),
    StatusCommand(),
    RememberCommand(),
    MemoryCommand(),
    ForgetCommand(),
    UserCommand(),
    ApproveCommand(),
    RemoveCommand(),
    NameCommand(),
    LinkCommand(),
    MCPCommand(),
    TaskCommand(),
    GrantCommand(),
    RevokeCommand(),
    PermsCommand(),
    AclCommand(),
    SkillCommand(),
    GrowCommand(),
    ModelCommand(),
    EffortCommand(),
    StyleCommand(),
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


def _is_enabled(config: GaiaConfig | None, name: str) -> bool:
    """A command is on unless ``gaia.yaml`` lists it with ``enabled: false``."""
    if config is None:
        return True
    entry = config.commands.get(name)
    return True if entry is None else entry.enabled


def default_registry(config: GaiaConfig | None = None) -> CommandRegistry:
    """Build the registry with every built-in command enabled by ``config``."""
    registry = CommandRegistry()
    for command in _BUILTINS:
        if _is_enabled(config, command.name):
            registry.register(command)
    return registry

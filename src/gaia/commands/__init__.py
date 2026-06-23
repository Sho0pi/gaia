"""In-chat slash commands — ``/help``, ``/reset``, ``/forget``, ….

A command runs *instead of* the LLM when a message starts with ``/``: the handler parses
it, runs the matching :class:`Command`, and sends the reply directly — no model call, no
memory ingest. See :mod:`gaia.commands.registry` for the built-in set.
"""

from gaia.commands.base import Command, CommandContext, authorize, parse
from gaia.commands.registry import CommandRegistry, default_registry

__all__ = [
    "Command",
    "CommandContext",
    "CommandRegistry",
    "authorize",
    "default_registry",
    "parse",
]

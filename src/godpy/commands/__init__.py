"""In-chat slash commands — ``/help``, ``/reset``, ``/forget``, ….

A command runs *instead of* the LLM when a message starts with ``/``: the handler parses
it, runs the matching :class:`Command`, and sends the reply directly — no model call, no
memory ingest. See :mod:`godpy.commands.registry` for the built-in set.
"""

from godpy.commands.base import Command, CommandContext, parse
from godpy.commands.registry import CommandRegistry, default_registry

__all__ = ["Command", "CommandContext", "CommandRegistry", "default_registry", "parse"]

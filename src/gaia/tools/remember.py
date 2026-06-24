"""The ``remember`` tool: store a fact in long-term memory on purpose.

ADK's ``load_memory`` tool reads memory; this is the write side. Auto-ingest already
feeds every turn to mem0, but ``remember`` lets the agent deliberately pin a single
durable fact ("the user's timezone is IST") so it is kept verbatim, not just inferred.

The write goes through the ``Runner``'s memory service (the same one ``load_memory``
searches) via ADK's public ``ToolContext.add_memory`` — it scopes the write to the live
session's user, so the fact lands in the right store. ``add_memory`` raises ``ValueError``
when no memory service is configured; we translate that to the friendly "disabled" result.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.memory.memory_entry import MemoryEntry
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from gaia.tools._helpers import err, ok

#: Tool id, used by the registry and as the ADK tool name (matches the closure name).
NAME = "remember"


def make_remember() -> Callable[..., Any]:
    """Return the ADK ``remember`` tool.

    ADK reads the returned function's name, signature and docstring to build the tool
    schema, so the closure's name matches :data:`NAME` and documents its arg + return.
    """

    async def remember(fact: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Save a durable fact about the user to long-term memory (stable preferences
        and details worth recalling later — not passing chit-chat).

        Args:
            fact: a short, self-contained statement (e.g. "the user's timezone is IST").
        """
        fact = fact or ""  # a model may send null, not the default
        cleaned = fact.strip()

        if not cleaned:
            return err("fact must not be empty")

        entry = MemoryEntry(content=types.Content(parts=[types.Part(text=cleaned)]), author="user")
        try:
            await tool_context.add_memory(memories=[entry])
        except ValueError:  # ADK raises when no memory service is configured
            return err("long-term memory is disabled")
        return ok(fact=cleaned)

    return remember

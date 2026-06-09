"""The ``remember`` tool: store a fact in long-term memory on purpose.

ADK's ``load_memory`` tool reads memory; this is the write side. Auto-ingest already
feeds every turn to mem0, but ``remember`` lets the agent deliberately pin a single
durable fact ("the user's timezone is IST") so it is kept verbatim, not just inferred.

The write goes through the ``Runner``'s memory service (the same one ``load_memory``
searches), reached via ``tool_context``; ``app_name``/``user_id`` come from the live
invocation so the fact lands in the right user's store.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.memory.memory_entry import MemoryEntry
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from godpy.logs import log_event

#: Tool id, used by the registry and as the ADK tool name (matches the closure name).
NAME = "remember"


def make_remember() -> Callable[..., Any]:
    """Return the ADK ``remember`` tool.

    ADK reads the returned function's name, signature and docstring to build the tool
    schema, so the closure's name matches :data:`NAME` and documents its arg + return.
    """

    async def remember(fact: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Save a durable fact about the user to long-term memory.

        Use this for stable preferences or details worth recalling in future
        conversations (e.g. "the user's timezone is IST"), not for passing chit-chat.

        Args:
            fact (str): The fact to remember, as a short self-contained statement.

        Returns:
            dict: On success {'status': 'success', 'fact': str}. On failure
            {'status': 'error', 'error_message': str}.
        """
        cleaned = fact.strip()

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event("tool_used", tool=NAME, fact=cleaned, status=result["status"])
            return result

        if not cleaned:
            return done({"status": "error", "error_message": "fact must not be empty"})

        ctx = tool_context._invocation_context
        if ctx.memory_service is None:
            return done({"status": "error", "error_message": "long-term memory is disabled"})

        entry = MemoryEntry(content=types.Content(parts=[types.Part(text=cleaned)]), author="user")
        await ctx.memory_service.add_memory(
            app_name=ctx.app_name, user_id=ctx.user_id, memories=[entry]
        )
        return done({"status": "success", "fact": cleaned})

    return remember

"""Long-term memory backed by mem0.

mem0 is a framework-agnostic memory layer: it extracts and stores facts across
sessions so God gets better at recalling the user day by day. The mem0 import is
deferred so this module imports cleanly without a configured vector store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from godpy.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mem0 import Memory


class LongTermMemory:
    """Wrapper over a mem0 ``Memory`` instance, scoped per user."""

    def __init__(self, memory: Memory | None = None) -> None:
        self._memory = memory

    @property
    def backend(self) -> Memory:
        """Lazily build the mem0 backend on first use."""
        if self._memory is None:
            from mem0 import Memory

            self._memory = Memory()
        return self._memory

    def remember(self, messages: list[dict[str, str]], *, user_id: str) -> Any:
        """Let mem0 extract and persist facts from a conversation slice."""
        result = self.backend.add(messages, user_id=user_id)
        log_event("memory_updated", user=user_id, messages=len(messages))
        return result

    def recall(self, query: str, *, user_id: str, limit: int = 5) -> Any:
        """Retrieve the most relevant stored facts for ``query``."""
        return self.backend.search(query, user_id=user_id, limit=limit)

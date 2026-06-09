"""Adapt mem0 to ADK's :class:`BaseMemoryService` so it drops into a ``Runner``.

ADK already defines the long-term memory contract (ingest a conversation, search it
back) and ships the ``load_memory`` tool that calls it — so godpy reuses that plumbing
and only supplies the mem0 implementation here. Short-term memory is ADK's own session
state; this service is the long-term tier that survives across sessions.

The mem0 client is injected (built lazily via :func:`godpy.memory.backend.build_mem0`),
so unit tests pass a fake and nothing here imports a vector store.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from godpy.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.events.event import Event
    from google.adk.sessions.session import Session

#: How many memories ``search_memory`` returns by default.
DEFAULT_RECALL_LIMIT = 5

#: ADK content roles → the roles mem0 expects on its message dicts.
_ROLE_MAP = {"model": "assistant", "user": "user"}


class Mem0Client(Protocol):
    """The slice of mem0's ``Memory`` API this service uses."""

    def add(self, messages: list[dict[str, str]], **kwargs: Any) -> Any: ...

    def search(self, query: str, **kwargs: Any) -> Any: ...


def _text_of(content: types.Content | None) -> str:
    """Join the text parts of an ADK ``Content`` into one string."""
    if content is None or not content.parts:
        return ""
    return "".join(part.text for part in content.parts if part.text).strip()


def _events_to_messages(events: Sequence[Event]) -> list[dict[str, str]]:
    """Map ADK events to mem0 ``{role, content}`` messages, dropping empty turns."""
    messages: list[dict[str, str]] = []
    for event in events:
        text = _text_of(event.content)
        if not text:
            continue
        role = event.content.role if event.content else None
        messages.append({"role": _ROLE_MAP.get(role or "", "user"), "content": text})
    return messages


class Mem0MemoryService(BaseMemoryService):
    """Long-term memory backed by mem0, scoped per user.

    mem0 auto-extracts durable facts from the conversation it is given (``infer=True``)
    so the store grows and de-duplicates itself day by day; explicit writes from the
    ``remember`` tool are stored verbatim (``infer=False``).
    """

    def __init__(self, backend: Mem0Client, *, recall_limit: int = DEFAULT_RECALL_LIMIT) -> None:
        self._backend = backend
        self._recall_limit = recall_limit

    async def add_session_to_memory(self, session: Session) -> None:
        """Ingest a whole session — every turn so far — into long-term memory."""
        self._ingest(
            _events_to_messages(session.events),
            user_id=session.user_id,
            run_id=session.id,
        )

    async def add_events_to_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        events: Sequence[Event],
        session_id: str | None = None,
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Ingest just the latest turn's events (the incremental auto-ingest path)."""
        self._ingest(_events_to_messages(events), user_id=user_id, run_id=session_id)

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[MemoryEntry],
        custom_metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Store explicit facts verbatim — used by the ``remember`` tool."""
        messages = [
            {"role": "user", "content": text}
            for entry in memories
            if (text := _text_of(entry.content))
        ]
        self._ingest(messages, user_id=user_id, infer=False)

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        """Return the most relevant stored facts for ``query`` as ADK memories."""
        raw = self._backend.search(query, filters={"user_id": user_id}, top_k=self._recall_limit)
        results = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
        memories = [
            MemoryEntry(
                content=types.Content(parts=[types.Part(text=hit["memory"])]),
                author="user",
                id=hit.get("id"),
                timestamp=hit.get("updated_at") or hit.get("created_at"),
            )
            for hit in results
            if hit.get("memory")
        ]
        return SearchMemoryResponse(memories=memories)

    def _ingest(
        self, messages: list[dict[str, str]], *, user_id: str, run_id: str | None = None, **kw: Any
    ) -> None:
        """Hand messages to mem0 and log the write; a no-op when there's nothing to add."""
        if not messages:
            return
        kwargs: dict[str, Any] = {"user_id": user_id, **kw}
        if run_id:
            kwargs["run_id"] = run_id
        self._backend.add(messages, **kwargs)
        log_event("memory_updated", user=user_id, messages=len(messages))

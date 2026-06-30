"""Adapt mem0 to ADK's :class:`BaseMemoryService` so it drops into a ``Runner``.

ADK already defines the long-term memory contract (ingest a conversation, search it
back) and ships the ``load_memory`` tool that calls it — so gaia reuses that plumbing
and only supplies the mem0 implementation here. Short-term memory is ADK's own session
state; this service is the long-term tier that survives across sessions.

The mem0 client is injected (built lazily via :func:`gaia.memory.backend.build_mem0`),
so unit tests pass a fake and nothing here imports a vector store.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Protocol

from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from gaia.logs import log_event

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

    def get_all(self, **kwargs: Any) -> Any: ...

    def delete_all(self, **kwargs: Any) -> Any: ...


def _text_of(content: types.Content | None) -> str:
    """Join the text parts of an ADK ``Content`` into one string."""
    if content is None or not content.parts:
        return ""
    return "".join(part.text for part in content.parts if part.text).strip()


def _events_to_messages(events: Sequence[Event]) -> list[dict[str, str]]:
    """Map ADK events to mem0 ``{role, content}`` messages, dropping empty turns.

    ADK only emits two content roles (verified in ``adk.flows.llm_flows.contents`` and against real
    session events): ``user`` (human turns; also tool *responses*, text-less) and ``model``
    (assistant turns; also tool *calls*, text-less). So the text-bearing turns are always
    ``user``/``model`` — the empty-text guard already drops the text-less call/response events. We
    map those two and **drop any other role** rather than the old ``get(role, "user")`` default, which
    would mislabel a future/synthetic ``tool``/``system`` event as something the human said — the
    assistant-action-log noise ``backend.py`` fights.
    """
    messages: list[dict[str, str]] = []
    for event in events:
        text = _text_of(event.content)
        if not text:
            continue
        role = event.content.role if event.content else None
        mem_role = _ROLE_MAP.get(role or "")
        if mem_role is None:
            continue
        messages.append({"role": mem_role, "content": text})
    return messages


class Mem0MemoryService(BaseMemoryService):
    """Long-term memory backed by mem0, scoped per user.

    mem0 auto-extracts durable facts from the conversation it is given (``infer=True``)
    so the store grows and de-duplicates itself day by day; explicit writes from the
    ``remember`` tool are stored verbatim (``infer=False``).

    ``app_name`` is part of ADK's ``BaseMemoryService`` contract and is accepted on
    every method, but it's unused here: mem0 scopes by ``user_id`` (plus ``run_id``),
    which is enough for one app. It would only matter to namespace a store shared across
    multiple apps — then it'd map to a per-app ``collection_name`` or filter.
    """

    def __init__(self, backend: Mem0Client, *, recall_limit: int = DEFAULT_RECALL_LIMIT) -> None:
        self._backend = backend
        self._recall_limit = recall_limit
        # mem0.add() does blocking LLM-extract + embed network calls; run them OFF the event loop
        # so a slow provider (Gemini on a Pi) never freezes the daemon — and so the shutdown flush
        # can be time-boxed instead of stalling Ctrl-C (#300). A dedicated single-worker pool (not
        # asyncio's default executor) keeps writes serialized AND keeps a wedged write out of
        # asyncio's loop-close join, so shutdown stays fast.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem0")

    async def aclose(self) -> None:
        """Drop the ingest pool without waiting — a wedged write is abandoned (best-effort)."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _off_loop(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a blocking mem0 call on the dedicated pool, not the event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(fn, *args, **kwargs))

    async def add_session_to_memory(self, session: Session) -> None:
        """Ingest a whole session — every turn so far — into long-term memory."""
        await self._off_loop(
            self._ingest,
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
        await self._off_loop(
            self._ingest, _events_to_messages(events), user_id=user_id, run_id=session_id
        )

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
        await self._off_loop(self._ingest, messages, user_id=user_id, infer=False)

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

    async def list_memories(self, *, user_id: str) -> list[str]:
        """Return every stored memory text for ``user_id`` (newest mem0 returns first)."""
        raw = self._backend.get_all(filters={"user_id": user_id})
        results = raw.get("results", []) if isinstance(raw, dict) else (raw or [])
        return [hit["memory"] for hit in results if hit.get("memory")]

    async def forget(self, *, user_id: str) -> int:
        """Wipe all of ``user_id``'s long-term memory; return how many were removed."""
        removed = len(await self.list_memories(user_id=user_id))
        self._backend.delete_all(user_id=user_id)
        log_event("memory_forgotten", user=user_id, removed=removed)
        return removed

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

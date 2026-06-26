"""Mem0MemoryService adapts mem0 to ADK without importing a vector store.

A fake mem0 client records ``add`` calls and returns canned ``search`` hits, so the
event->message mapping, the hit->MemoryEntry mapping and the write logging are all
checked offline.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from gaia.memory.service import Mem0MemoryService


class _FakeMem0:
    """Records add() calls; returns a preset search payload."""

    def __init__(self, search_result: Any = None, all_result: Any = None) -> None:
        self.added: list[tuple[list[dict[str, str]], dict[str, Any]]] = []
        self._search_result = search_result if search_result is not None else {"results": []}
        self._all_result = all_result if all_result is not None else {"results": []}
        self.deleted: list[dict[str, Any]] = []

    def add(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.added.append((messages, kwargs))
        return {"results": []}

    def search(self, query: str, **kwargs: Any) -> Any:
        self.last_search = (query, kwargs)
        return self._search_result

    def get_all(self, **kwargs: Any) -> Any:
        self.last_get_all = kwargs
        return self._all_result

    def delete_all(self, **kwargs: Any) -> Any:
        self.deleted.append(kwargs)


def _event(text: str, role: str) -> SimpleNamespace:
    return SimpleNamespace(content=types.Content(role=role, parts=[types.Part(text=text)]))


async def test_add_session_maps_events_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr("gaia.memory.service.log_event", lambda a, **k: events.append((a, k)))
    backend = _FakeMem0()
    service = Mem0MemoryService(backend)
    session = SimpleNamespace(
        events=[_event("I live in Berlin", "user"), _event("Noted.", "model")],
        user_id="u1",
        id="s1",
    )

    await service.add_session_to_memory(session)  # type: ignore[arg-type]

    messages, kwargs = backend.added[0]
    assert messages == [
        {"role": "user", "content": "I live in Berlin"},
        {"role": "assistant", "content": "Noted."},  # ADK "model" -> mem0 "assistant"
    ]
    assert kwargs["user_id"] == "u1" and kwargs["run_id"] == "s1"
    assert ("memory_updated", {"user": "u1", "messages": 2}) in events


async def test_search_maps_hits_to_memory_entries() -> None:
    backend = _FakeMem0(
        {"results": [{"id": "m1", "memory": "timezone is IST", "updated_at": "2026-06-09"}]}
    )
    service = Mem0MemoryService(backend, recall_limit=3)

    response = await service.search_memory(app_name="gaia", user_id="u1", query="tz")

    assert backend.last_search == ("tz", {"filters": {"user_id": "u1"}, "top_k": 3})
    (entry,) = response.memories
    assert entry.content.parts[0].text == "timezone is IST"
    assert entry.id == "m1" and entry.timestamp == "2026-06-09"


async def test_search_accepts_bare_list_payload() -> None:
    backend = _FakeMem0([{"id": "m1", "memory": "likes tea"}])
    service = Mem0MemoryService(backend)

    response = await service.search_memory(app_name="gaia", user_id="u1", query="drink")

    assert response.memories[0].content.parts[0].text == "likes tea"


async def test_add_memory_stores_verbatim() -> None:
    backend = _FakeMem0()
    service = Mem0MemoryService(backend)
    entry = MemoryEntry(content=types.Content(parts=[types.Part(text="uses vim")]))

    await service.add_memory(app_name="gaia", user_id="u1", memories=[entry])

    messages, kwargs = backend.added[0]
    assert messages == [{"role": "user", "content": "uses vim"}]
    assert kwargs["infer"] is False  # explicit facts are not re-inferred


async def test_list_memories_returns_texts() -> None:
    backend = _FakeMem0(
        all_result={"results": [{"memory": "likes teal"}, {"memory": "owns a cat"}, {}]}
    )
    service = Mem0MemoryService(backend)

    items = await service.list_memories(user_id="u1")

    assert items == ["likes teal", "owns a cat"]  # the field-less hit is skipped
    assert backend.last_get_all == {"filters": {"user_id": "u1"}}


async def test_forget_counts_then_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[Any] = []
    monkeypatch.setattr("gaia.memory.service.log_event", lambda a, **k: logged.append((a, k)))
    backend = _FakeMem0(all_result={"results": [{"memory": "a"}, {"memory": "b"}]})
    service = Mem0MemoryService(backend)

    removed = await service.forget(user_id="u1")

    assert removed == 2
    assert backend.deleted == [{"user_id": "u1"}]
    assert ("memory_forgotten", {"user": "u1", "removed": 2}) in logged


async def test_empty_events_are_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[Any] = []
    monkeypatch.setattr("gaia.memory.service.log_event", lambda a, **k: logged.append(a))
    backend = _FakeMem0()
    service = Mem0MemoryService(backend)

    await service.add_events_to_memory(app_name="gaia", user_id="u1", events=[])

    assert backend.added == [] and logged == []


async def test_ingest_runs_off_the_event_loop() -> None:
    # mem0.add() blocks (network LLM+embed); it must run on the dedicated pool, not the loop, so a
    # slow provider can't freeze the daemon / stall the shutdown flush (#300).
    import threading

    seen: dict[str, int] = {}

    class _ThreadAwareMem0(_FakeMem0):
        def add(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
            seen["thread"] = threading.get_ident()
            return super().add(messages, **kwargs)

    backend = _ThreadAwareMem0()
    service = Mem0MemoryService(backend)
    entry = MemoryEntry(content=types.Content(parts=[types.Part(text="hi")]))
    await service.add_memory(app_name="gaia", user_id="u1", memories=[entry])

    assert backend.added  # the write happened
    assert seen["thread"] != threading.get_ident()  # ran on the pool, not the loop thread
    await service.aclose()  # drops the pool without raising


async def test_aclose_is_idempotent() -> None:
    service = Mem0MemoryService(_FakeMem0())
    await service.aclose()
    await service.aclose()  # second call is a no-op, never raises

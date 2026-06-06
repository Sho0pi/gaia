"""Short-term memory is a simple, isolated per-session scratchpad."""

from __future__ import annotations

from godpy.memory import ShortTermMemory


def test_set_get() -> None:
    mem = ShortTermMemory()
    mem.set("topic", "billing")
    assert mem.get("topic") == "billing"


def test_get_default() -> None:
    assert ShortTermMemory().get("missing", "fallback") == "fallback"


def test_seeded_state_is_copied() -> None:
    seed = {"a": 1}
    mem = ShortTermMemory(seed)
    mem.set("a", 2)
    assert seed["a"] == 1  # original not mutated


def test_clear() -> None:
    mem = ShortTermMemory({"a": 1})
    mem.clear()
    assert mem.as_dict() == {}

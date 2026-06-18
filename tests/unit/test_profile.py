"""gaia.memory.profile.distill_profile — session-start user-profile distillation.

Offline: the memory service, task board and the profiler LLM call are faked. The real
``_run_profiler`` (a nested Runner) is never invoked here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import gaia.memory.profile as profile_mod
from gaia.memory.profile import distill_profile


def _gaia(facts: list[str], tasks: list[object]) -> SimpleNamespace:
    async def list_memories(*, user_id: str) -> list[str]:
        return facts

    return SimpleNamespace(
        memory_service=SimpleNamespace(list_memories=list_memories),
        tasks=SimpleNamespace(list=lambda *, owner: tasks),
    )


def _task(title: str, status: str = "inbox") -> SimpleNamespace:
    return SimpleNamespace(title=title, status=SimpleNamespace(value=status))


async def test_distill_runs_profiler_over_facts_and_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def fake_profiler(gaia: object, facts: list[str], projects: list[str]) -> str:
        seen["facts"], seen["projects"] = facts, projects
        return "- Name: Itay\n- builds gaia"

    monkeypatch.setattr(profile_mod, "_run_profiler", fake_profiler)
    gaia = _gaia(facts=["name is Itay"], tasks=[_task("gym site", "running")])

    block = await distill_profile(gaia, "u1")

    assert block == "- Name: Itay\n- builds gaia"
    assert seen["facts"] == ["name is Itay"]
    assert seen["projects"] == ["gym site (running)"]  # title (status), newest-first


async def test_distill_is_noop_without_facts_or_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_profiler(*a: object, **k: object) -> str:
        nonlocal called
        called = True
        return "x"

    monkeypatch.setattr(profile_mod, "_run_profiler", fake_profiler)

    assert await distill_profile(_gaia(facts=[], tasks=[]), "u1") is None
    assert not called  # empty store must never trigger the model


async def test_distill_falls_back_to_raw_facts_on_profiler_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*a: object, **k: object) -> str:
        raise RuntimeError("no model key")

    monkeypatch.setattr(profile_mod, "_run_profiler", boom)
    gaia = _gaia(facts=["likes tea", "name is Maya"], tasks=[])

    block = await distill_profile(gaia, "u1")

    assert block == "- likes tea\n- name is Maya"  # graceful: raw facts, never empty


async def test_distill_none_when_memory_off() -> None:
    gaia = SimpleNamespace(memory_service=None)

    assert await distill_profile(gaia, "u1") is None

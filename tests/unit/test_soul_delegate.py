"""delegate_to_soul: forge/reuse routing, file listing, dict shape, logging.

The soul-smith decision and the nested soul run are both stubbed, so the orchestration
is exercised without a model backend.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import godpy.souls.delegate as delegate
from godpy import constants
from godpy.agents import AgentRegistry, AgentSpec
from godpy.souls import make_delegate
from godpy.souls.smith import SoulDecision
from godpy.tools.fs.base import sandbox_for

_SPEC = AgentSpec(name="Web Designer", description="Builds websites.", instruction="i", model="m")


class _FakeFactory:
    """Persists a new spec then returns a stand-in soul (no real ADK build)."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def create_or_reuse(self, spec: AgentSpec) -> Any:
        if self._registry.get(spec.key) is None:
            self._registry.save(spec)
        return SimpleNamespace(name=spec.key)


def _god(registry: AgentRegistry) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(llm=SimpleNamespace(model="m")),
        settings=SimpleNamespace(model="m"),
        registry=registry,
        factory=_FakeFactory(registry),
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any]]:
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    events: list[Any] = []
    monkeypatch.setattr(delegate, "log_event", lambda a, **k: events.append((a, k)))
    god = _god(AgentRegistry(tmp_path / "reg"))
    return god, events


def _stub_decision(monkeypatch: pytest.MonkeyPatch, decision: SoulDecision) -> None:
    async def fake_decide(*_a: Any, **_k: Any) -> SoulDecision:
        return decision

    monkeypatch.setattr(delegate, "_decide", fake_decide)


def _stub_run_writing(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    async def fake_run(soul: Any, key: str, task: str) -> str:
        (sandbox_for(constants.AGENTS_DIR, key).primary / filename).write_text("<html>")
        return "built it"

    monkeypatch.setattr(delegate, "_run_soul", fake_run)


async def test_forge_path_persists_runs_and_lists_files(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    god, events = env
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="none fit", spec=_SPEC))
    _stub_run_writing(monkeypatch, "index.html")

    out = await make_delegate(god)("design a site", tool_context=None)

    assert out["status"] == "success"
    assert out["created"] is True
    assert out["soul"] == "Web Designer"
    assert out["files"] == ["index.html"]
    assert out["workspace"].endswith("web-designer/workspace")
    assert god.registry.get("web-designer") is not None  # persisted for reuse
    assert events[0] == (
        "tool_used",
        {"tool": "delegate_to_soul", "soul": "Web Designer", "created": True, "status": "success"},
    )


async def test_reuse_path_does_not_recreate(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    god, _ = env
    god.registry.save(_SPEC)  # already known
    _stub_decision(
        monkeypatch, SoulDecision(action="reuse", reason="fits", soul_key="web-designer")
    )
    _stub_run_writing(monkeypatch, "index.html")

    out = await make_delegate(god)("another site", tool_context=None)

    assert out["status"] == "success"
    assert out["created"] is False
    assert out["soul"] == "Web Designer"


async def test_bad_reuse_key_is_a_graceful_error(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    god, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="reuse", reason="x", soul_key="ghost"))

    out = await make_delegate(god)("task", tool_context=None)

    assert out["status"] == "error"
    assert "usable decision" in out["error_message"]


async def test_smith_failure_is_caught(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    god, _ = env

    async def boom(*_a: Any, **_k: Any) -> SoulDecision:
        raise RuntimeError("model down")

    monkeypatch.setattr(delegate, "_decide", boom)

    out = await make_delegate(god)("task", tool_context=None)

    assert out["status"] == "error"
    assert "soul-smith failed" in out["error_message"]

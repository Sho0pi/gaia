"""delegate_to_soul: forge/reuse routing, file listing, dict shape, logging.

The soul-smith decision and the nested soul run are both stubbed, so the orchestration
is exercised without a model backend.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import gaia.souls.delegate as delegate
from gaia import constants
from gaia.agents import AgentSpec, SoulRegistry
from gaia.souls import make_delegate
from gaia.souls.smith import SoulDecision
from gaia.tools.fs.base import sandbox_for

_SPEC = AgentSpec(name="Web Designer", description="Builds websites.", instruction="i", model="m")


class _FakeFactory:
    """Persists a new spec then returns a stand-in soul (no real ADK build)."""

    def __init__(self, registry: SoulRegistry) -> None:
        self._registry = registry

    def create_or_reuse(self, spec: AgentSpec) -> Any:
        if self._registry.get(spec.key) is None:
            self._registry.save(spec)
        return SimpleNamespace(name=spec.key)


def _gaia(registry: SoulRegistry) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            llm=SimpleNamespace(
                model="m", provider="gemini", openai=SimpleNamespace(use_oauth=False)
            ),
            souls=SimpleNamespace(timeout_seconds=300.0),
        ),
        settings=SimpleNamespace(model="m"),
        souls=registry,
        factory=_FakeFactory(registry),
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any]]:
    # Tool-call logging now lives in ToolLoggingPlugin (tested in test_tool_logging_plugin);
    # delegate_to_soul no longer self-logs. ``events`` stays empty for signature stability.
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    events: list[Any] = []
    gaia = _gaia(SoulRegistry(tmp_path / "reg"))
    return gaia, events


def _stub_decision(monkeypatch: pytest.MonkeyPatch, decision: SoulDecision) -> None:
    async def fake_decide(*_a: Any, **_k: Any) -> SoulDecision:
        return decision

    monkeypatch.setattr(delegate, "_decide", fake_decide)


def _stub_run_writing(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    async def fake_run(gaia: Any, soul: Any, key: str, task: str, user_id: str) -> str:
        (sandbox_for(constants.AGENTS_DIR, key).primary / filename).write_text("<html>")
        return "built it"

    monkeypatch.setattr(delegate, "_run_soul", fake_run)


async def test_forge_path_persists_runs_and_lists_only_new_files(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    # An unrelated deliverable from a previous task already sits in the workspace.
    old = sandbox_for(constants.AGENTS_DIR, "web_designer").primary / "old_site.html"
    old.write_text("old")
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="none fit", spec=_SPEC))
    _stub_run_writing(monkeypatch, "index.html")

    out = await make_delegate(gaia)("design a site", tool_context=None)

    assert out["status"] == "success"
    assert out["created"] is True
    assert out["soul"] == "Web Designer"
    assert out["files"] == ["index.html"]  # only this run's file; old_site.html excluded
    assert out["workspace"].endswith("web_designer/workspace")
    assert gaia.souls.get("web_designer") is not None  # persisted for reuse


async def test_passes_invocation_user_id_to_the_soul(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))
    seen: dict[str, str] = {}

    async def fake_run(gaia: Any, soul: Any, key: str, task: str, user_id: str) -> str:
        seen["user_id"] = user_id
        return "ok"

    monkeypatch.setattr(delegate, "_run_soul", fake_run)
    ctx = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="alice"))

    await make_delegate(gaia)("task", tool_context=ctx)

    assert seen["user_id"] == "alice"  # the soul reads/writes the real user's memory


async def test_reuse_path_does_not_recreate(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    gaia.souls.save(_SPEC)  # already known
    _stub_decision(
        monkeypatch, SoulDecision(action="reuse", reason="fits", soul_key="web_designer")
    )
    _stub_run_writing(monkeypatch, "index.html")

    out = await make_delegate(gaia)("another site", tool_context=None)

    assert out["status"] == "success"
    assert out["created"] is False
    assert out["soul"] == "Web Designer"


async def test_bad_reuse_key_is_a_graceful_error(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="reuse", reason="x", soul_key="ghost"))

    out = await make_delegate(gaia)("task", tool_context=None)

    assert out["status"] == "error"
    assert "usable decision" in out["error_message"]


async def test_smith_failure_is_caught(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env

    async def boom(*_a: Any, **_k: Any) -> SoulDecision:
        raise RuntimeError("model down")

    monkeypatch.setattr(delegate, "_decide", boom)

    out = await make_delegate(gaia)("task", tool_context=None)

    assert out["status"] == "error"
    assert "soul-smith failed" in out["error_message"]


async def test_honors_configured_soul_timeout(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    gaia.config.souls.timeout_seconds = 0.01  # tiny budget so the slow run trips it
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))

    async def slow_run(gaia: Any, soul: Any, key: str, task: str, user_id: str) -> str:
        import asyncio

        await asyncio.sleep(1.0)
        return "too late"

    monkeypatch.setattr(delegate, "_run_soul", slow_run)

    out = await make_delegate(gaia)("task", tool_context=None)

    assert out["status"] == "error" and "timed out" in out["error_message"]

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
from gaia.souls.run import _AgentTurn
from gaia.souls.smith import SoulDecision
from gaia.tools.fs.base import sandbox_for

_SPEC = AgentSpec(name="Web Designer", description="Builds websites.", instruction="i", model="m")
#: Minimal stand-in for ADK's ToolContext (only ``user_id`` is read by delegate_to_soul).
_CTX = SimpleNamespace(user_id="")


class _FakeFactory:
    """Persists a new spec then returns a stand-in soul (no real ADK build)."""

    def __init__(self, registry: SoulRegistry) -> None:
        self._registry = registry

    def create_or_reuse(self, spec: AgentSpec, *, effort: str = "", extra_tools: Any = None) -> Any:
        if self._registry.get(spec.key) is None:
            self._registry.save(spec)
        return SimpleNamespace(name=spec.key)


def _gaia(registry: SoulRegistry) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            llm=SimpleNamespace(
                model="m", provider="gemini", effort="", openai=SimpleNamespace(use_oauth=False)
            ),
            souls=SimpleNamespace(timeout_seconds=300.0),
        ),
        settings=SimpleNamespace(model="m"),
        souls=registry,
        factory=_FakeFactory(registry),
        soul_sessions=SimpleNamespace(pin=lambda _k: None, unpin=lambda _k: None),
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
    async def fake_run(
        gaia: Any,
        soul: Any,
        key: str,
        task: str,
        user_id: str,
        *,
        state: Any = None,
        warm_key: Any = None,
    ) -> _AgentTurn:
        (sandbox_for(constants.AGENTS_DIR, key).primary / filename).write_text("<html>")
        return _AgentTurn("built it", [])

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", fake_run)


async def test_forge_path_persists_runs_and_lists_only_new_files(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    # An unrelated deliverable from a previous task already sits in the workspace.
    old = sandbox_for(constants.AGENTS_DIR, "web_designer").primary / "old_site.html"
    old.write_text("old")
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="none fit", spec=_SPEC))
    _stub_run_writing(monkeypatch, "index.html")

    out = await make_delegate(gaia)("design a site", tool_context=_CTX)

    assert out["status"] == "success"
    assert out["created"] is True
    assert out["soul"] == "Web Designer"
    assert out["files"] == ["index.html"]  # only this run's file; old_site.html excluded
    # No project given -> a fresh project dir under the soul's workspace.
    assert "web_designer/workspace/" in out["workspace"]
    assert gaia.souls.get("web_designer") is not None  # persisted for reuse


async def test_project_arg_scopes_the_workspace(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))
    _stub_run_writing(monkeypatch, "index.html")

    out = await make_delegate(gaia)("build it", "plant-shop", tool_context=_CTX)

    assert out["status"] == "success"
    assert out["project"] == "plant-shop"
    assert out["workspace"].endswith("web_designer/workspace/plant-shop")


async def test_media_deliverables_come_back_in_the_result(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The soul writes a PDF deliverable + the site source, and took a screenshot mid-run. The
    # PDF (a media file) and the screenshot (from the soul's event stream) come back as media;
    # the .html source does not — so the root can deliver them without re-doing the work.
    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))

    async def fake_run(
        gaia: Any,
        soul: Any,
        key: str,
        task: str,
        user_id: str,
        *,
        state: Any = None,
        warm_key: Any = None,
    ) -> _AgentTurn:
        primary = sandbox_for(constants.AGENTS_DIR, key).primary
        (primary / "plan.pdf").write_text("%PDF")
        (primary / "index.html").write_text("<html>")
        return _AgentTurn("done", ["/tmp/screenshot.png"])  # a screenshot the soul took this run

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", fake_run)

    out = await make_delegate(gaia)("build it", tool_context=_CTX)

    assert out["status"] == "success"
    media = out["media"]
    assert "/tmp/screenshot.png" in media  # screenshot from the soul's events
    assert any(p.endswith("plan.pdf") for p in media)  # PDF deliverable from the workspace
    assert not any(p.endswith("index.html") for p in media)  # site source is not media


async def test_attachment_is_copied_into_the_soul_workspace(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # delegate_to_soul(attachments=[...]) hands a prior soul's file to the next one: it lands in
    # the target's workspace before it runs (the copy happens in execute_decision, pre-run).
    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))
    src = sandbox_for(constants.AGENTS_DIR, "gym_bro").primary / "plan.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF")

    seen: dict[str, Path] = {}

    async def fake_run(
        gaia: Any,
        soul: Any,
        key: str,
        task: str,
        user_id: str,
        *,
        state: Any = None,
        warm_key: Any = None,
    ) -> _AgentTurn:
        seen["dest"] = sandbox_for(constants.AGENTS_DIR, key).primary / "plan.pdf"
        return _AgentTurn("done", [])

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", fake_run)

    out = await make_delegate(gaia)(
        "build a site", "site", attachments=[str(src)], tool_context=_CTX
    )

    assert out["status"] == "success"
    assert seen["dest"].read_bytes() == b"%PDF"  # present in the soul's workspace before it ran


async def test_passes_invocation_user_id_to_the_soul(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))
    seen: dict[str, str] = {}

    async def fake_run(
        gaia: Any,
        soul: Any,
        key: str,
        task: str,
        user_id: str,
        *,
        state: Any = None,
        warm_key: Any = None,
    ) -> _AgentTurn:
        seen["user_id"] = user_id
        return _AgentTurn("ok", [])

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", fake_run)
    ctx = SimpleNamespace(user_id="alice")  # ADK public ToolContext.user_id

    await make_delegate(gaia)("task", tool_context=ctx)

    assert seen["user_id"] == "alice"  # the soul reads/writes the real user's memory


async def test_soul_pause_returns_none_and_appends_to_the_sink(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # When the soul calls ask_user, the run pauses: delegate_to_soul returns None (so the
    # long-running root pauses), appends the question to the per-turn sink, and pins the session.
    from gaia.core.elicit import soul_elicitation_sink

    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))
    pinned: list[str] = []
    gaia.soul_sessions = SimpleNamespace(pin=lambda k: pinned.append(k), unpin=lambda _k: None)

    async def fake_run(*_a: Any, **_k: Any) -> _AgentTurn:
        call = SimpleNamespace(
            id="soul-fc",
            args={"question": "What is your API key?", "options": None, "secret": True},
        )
        return _AgentTurn("", [], paused=call)

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", fake_run)
    sink: list[Any] = []
    token = soul_elicitation_sink.set(sink)  # the handler installs this before each turn
    try:
        out = await make_delegate(gaia)("build an app", "proj", tool_context=SimpleNamespace())
    finally:
        soul_elicitation_sink.reset(token)

    assert out is None  # long-running pause, not a result dict
    assert len(sink) == 1
    soul = sink[0]
    assert soul.question == "What is your API key?" and soul.secret is True
    assert soul.soul_key == _SPEC.key and soul.soul_fc_id == "soul-fc"
    assert pinned == [soul.warm_key]  # its warm session is protected from the reaper


async def test_reuse_path_does_not_recreate(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    gaia.souls.save(_SPEC)  # already known
    _stub_decision(
        monkeypatch, SoulDecision(action="reuse", reason="fits", soul_key="web_designer")
    )
    _stub_run_writing(monkeypatch, "index.html")

    out = await make_delegate(gaia)("another site", tool_context=_CTX)

    assert out["status"] == "success"
    assert out["created"] is False
    assert out["soul"] == "Web Designer"


async def test_bad_reuse_key_is_a_graceful_error(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    _stub_decision(monkeypatch, SoulDecision(action="reuse", reason="x", soul_key="ghost"))

    out = await make_delegate(gaia)("task", tool_context=_CTX)

    assert out["status"] == "error"
    assert "usable decision" in out["error_message"]


async def test_smith_failure_is_caught(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env

    async def boom(*_a: Any, **_k: Any) -> SoulDecision:
        raise RuntimeError("model down")

    monkeypatch.setattr(delegate, "_decide", boom)

    out = await make_delegate(gaia)("task", tool_context=_CTX)

    assert out["status"] == "error"
    assert "soul-smith failed" in out["error_message"]


async def test_honors_configured_soul_timeout(
    env: tuple[Any, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia, _ = env
    gaia.config.souls.timeout_seconds = 0.01  # tiny budget so the slow run trips it
    _stub_decision(monkeypatch, SoulDecision(action="forge", reason="r", spec=_SPEC))

    async def slow_run(
        gaia: Any,
        soul: Any,
        key: str,
        task: str,
        user_id: str,
        *,
        state: Any = None,
        warm_key: Any = None,
    ) -> _AgentTurn:
        import asyncio

        await asyncio.sleep(1.0)
        return _AgentTurn("too late", [])

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", slow_run)

    out = await make_delegate(gaia)("task", tool_context=None)

    assert out["status"] == "error" and "timed out" in out["error_message"]

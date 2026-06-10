"""The soul-smith decision schema + agent construction."""

from __future__ import annotations

import pytest

from godpy.agents.spec import AgentSpec
from godpy.souls.smith import SoulDecision, build_soul_smith


def test_decision_roundtrips_forge_with_nested_spec() -> None:
    d = SoulDecision(
        action="forge",
        reason="no soul fits",
        spec=AgentSpec(name="Web Designer", description="d", instruction="i", model="m"),
    )

    assert d.action == "forge"
    assert d.spec is not None and d.spec.key == "web_designer"
    assert d.soul_key is None


def test_decision_roundtrips_reuse() -> None:
    d = SoulDecision(action="reuse", reason="fits", soul_key="web_designer")

    assert d.action == "reuse" and d.soul_key == "web_designer" and d.spec is None


def test_reuse_without_soul_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="reuse"):
        SoulDecision(action="reuse", reason="r")


def test_forge_without_spec_is_rejected() -> None:
    with pytest.raises(ValueError, match="forge"):
        SoulDecision(action="forge", reason="r")


def test_build_soul_smith_is_schema_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.adk.agents as adk

    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(adk, "LlmAgent", _Recorder)

    build_soul_smith("gemini-x")

    assert captured["name"] == "soul_smith"
    assert captured["output_schema"] is SoulDecision
    assert captured.get("tools") is None  # pure decision agent, no tools
    assert captured["disallow_transfer_to_parent"] is True
    assert "gemini-x" in captured["instruction"]  # model interpolated into the prompt
    assert captured["model"] == "gemini-x"  # gemini stays a bare string


def test_build_soul_smith_resolves_openai_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.adk.agents as adk

    import godpy.models as models

    captured: dict[str, object] = {}
    monkeypatch.setattr(adk, "LlmAgent", lambda **kw: captured.update(kw))
    monkeypatch.setattr(
        models,
        "resolve_model",
        lambda model, *, provider, use_oauth: f"<{provider}:{model}:{use_oauth}>",
    )

    build_soul_smith("gpt-4o", "openai", use_oauth=True)

    assert captured["model"] == "<openai:gpt-4o:True>"  # provider + use_oauth routed through

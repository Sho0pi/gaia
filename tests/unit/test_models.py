"""resolve_model: Gemini stays a bare string; other providers wrap in LiteLlm."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from godpy.models import resolve_model


@pytest.fixture
def lite(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ADK's LiteLlm with a recorder so tests don't depend on litellm."""
    seen: dict[str, Any] = {}
    import google.adk.models.lite_llm as lm

    monkeypatch.setattr(
        lm, "LiteLlm", lambda *, model: seen.update(model=model) or SimpleNamespace(model=model)
    )
    return seen


def test_gemini_returns_bare_string() -> None:
    assert resolve_model("gemini-2.0-flash", "gemini") == "gemini-2.0-flash"


def test_openai_provider_wraps_litellm(lite: dict[str, Any]) -> None:
    out = resolve_model("gpt-4o", "openai")

    assert lite["model"] == "openai/gpt-4o"
    assert out.model == "openai/gpt-4o"


def test_already_prefixed_id_kept(lite: dict[str, Any]) -> None:
    resolve_model("openai/gpt-4o-mini", "openai")

    assert lite["model"] == "openai/gpt-4o-mini"  # not doubled


def test_provider_is_required() -> None:
    with pytest.raises(TypeError):
        resolve_model("gpt-4o")  # type: ignore[call-arg]  # provider must be explicit

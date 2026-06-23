"""resolve_model: Gemini stays a bare string; other providers wrap in LiteLlm; effort routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gaia.models import resolve_model, thinking_planner


@pytest.fixture
def lite(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ADK's LiteLlm with a recorder so tests don't depend on litellm."""
    seen: dict[str, Any] = {}
    import google.adk.models.lite_llm as lm

    def fake(*, model: str, **kwargs: Any) -> SimpleNamespace:
        seen.update(model=model, kwargs=kwargs)
        return SimpleNamespace(model=model, **kwargs)

    monkeypatch.setattr(lm, "LiteLlm", fake)
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


def test_openai_api_key_default_uses_litellm(lite: dict[str, Any]) -> None:
    out = resolve_model("gpt-4o", "openai")  # use_oauth defaults False

    assert lite["model"] == "openai/gpt-4o"  # LiteLLM (API key) path
    assert out.model == "openai/gpt-4o"


def test_openai_with_use_oauth_uses_chatgpt_backend() -> None:
    from gaia.providers.openai.responses_llm import ChatGptOAuthLlm

    out = resolve_model("gpt-5.5", "openai", use_oauth=True)
    assert isinstance(out, ChatGptOAuthLlm)
    assert out.model == "gpt-5.5"

    # the openai-chatgpt alias implies OAuth without the flag
    assert isinstance(resolve_model("gpt-5.5", "openai-chatgpt"), ChatGptOAuthLlm)


def test_provider_is_required() -> None:
    with pytest.raises(TypeError):
        resolve_model("gpt-4o")  # type: ignore[call-arg]  # provider must be explicit


# --- reasoning effort ---------------------------------------------------------------------


def test_litellm_path_passes_reasoning_effort(lite: dict[str, Any]) -> None:
    resolve_model("claude-sonnet-4-6", "anthropic", effort="high")
    assert lite["kwargs"] == {"reasoning_effort": "high"}  # litellm maps it per provider


def test_litellm_path_omits_effort_when_blank(lite: dict[str, Any]) -> None:
    resolve_model("gpt-4o", "openai", effort="")
    assert lite["kwargs"] == {}  # no reasoning_effort sent at the default


def test_oauth_backend_carries_effort() -> None:
    out = resolve_model("gpt-5.5", "openai", use_oauth=True, effort="medium")
    assert out.effort == "medium"  # ends up in the Responses reasoning.effort


def test_thinking_planner_for_gemini_maps_effort_to_budget() -> None:
    planner = thinking_planner("gemini", "gemini-2.5-flash", "high")
    assert planner is not None
    assert planner.thinking_config.thinking_budget == 24576


def test_thinking_planner_none_for_nonthinking_gemini() -> None:
    assert thinking_planner("gemini", "gemini-2.0-flash", "high") is None  # 2.0 has no budget


def test_thinking_planner_none_off_gemini_and_when_blank() -> None:
    assert thinking_planner("openai", "gpt-4o", "high") is None  # effort rides the model object
    assert thinking_planner("gemini", "gemini-2.5-flash", "") is None  # no effort set

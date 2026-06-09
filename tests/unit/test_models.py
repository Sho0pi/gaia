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
    assert resolve_model("gemini-2.5-flash") == "gemini-2.5-flash"
    assert resolve_model("gemini-2.0-flash", provider="gemini") == "gemini-2.0-flash"


def test_openai_provider_wraps_litellm(lite: dict[str, Any]) -> None:
    out = resolve_model("gpt-4o", provider="openai")

    assert lite["model"] == "openai/gpt-4o"
    assert out.model == "openai/gpt-4o"


def test_already_prefixed_id_kept(lite: dict[str, Any]) -> None:
    resolve_model("openai/gpt-4o-mini", provider="openai")

    assert lite["model"] == "openai/gpt-4o-mini"  # not doubled


def test_openai_chatgpt_provider_uses_oauth_backend() -> None:
    from godpy.providers.openai_chatgpt.responses_llm import ChatGptOAuthLlm

    out = resolve_model("gpt-5", provider="openai-chatgpt")
    assert isinstance(out, ChatGptOAuthLlm)
    assert out.model == "gpt-5"


def test_inference_without_provider(lite: dict[str, Any]) -> None:
    resolve_model("gpt-4o")
    assert lite["model"] == "openai/gpt-4o"

    resolve_model("claude-3-5-sonnet")
    assert lite["model"] == "anthropic/claude-3-5-sonnet"

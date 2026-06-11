"""resolve_model: Gemini stays a bare string; other providers wrap in LiteLlm."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gaia.models import resolve_model


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

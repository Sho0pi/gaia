"""Resolve a model id to something ADK's ``LlmAgent`` accepts.

ADK natively handles only Gemini model strings (``gemini-.*``). Every other provider runs
through ADK's LiteLLM wrapper. :func:`resolve_model` keeps Gemini as a bare string (no
behaviour change) and wraps anything else in a ``LiteLlm`` — so `llm: { provider: openai,
model: gpt-4o }` in ``god.yaml`` just works once ``OPENAI_API_KEY`` is in the env.

litellm is an optional dependency (the ``llm`` group); it's imported lazily and only when a
non-Gemini model is actually configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.models.base_llm import BaseLlm


def resolve_model(model: str, provider: str) -> str | BaseLlm:
    """Return a model usable by ``LlmAgent``: a bare string for Gemini, else a ``LiteLlm``.

    Both ``provider`` and ``model`` are required and come straight from ``llm.provider`` /
    ``llm.model`` — there's no guessing. A ``gemini`` provider returns the model string
    unchanged (ADK handles it natively); anything else is wrapped in a ``LiteLlm`` whose id
    is ``"<provider>/<model>"`` (unless ``model`` already carries a ``provider/`` prefix).
    Keys come from the provider's env var (e.g. ``OPENAI_API_KEY``).
    """
    prov = provider.lower()
    if prov == "gemini":
        return model

    if prov in ("openai-chatgpt", "chatgpt"):
        # Subscription auth (Sign in with ChatGPT) — its own ADK backend, not LiteLLM.
        from godpy.providers.openai_chatgpt import ChatGptOAuthLlm

        return ChatGptOAuthLlm(model=model)

    lite_id = model if "/" in model else f"{prov}/{model}"
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # litellm not installed
        raise RuntimeError(
            f"model {model!r} (provider {prov!r}) needs litellm — run: uv sync --group llm"
        ) from exc
    return LiteLlm(model=lite_id)

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
    from google.adk.models.lite_llm import LiteLlm


def resolve_model(model: str, provider: str) -> str | LiteLlm:
    """Build the model object ADK's ``LlmAgent(model=...)`` needs for ``provider``/``model``.

    The return type differs by provider because ADK only has a *native* backend for one of
    them — Gemini. ``provider`` and ``model`` are both required and taken verbatim from
    ``llm.provider`` / ``llm.model`` (no inference).

    * ``gemini`` -> the bare model **string**. ADK's model registry matches ``gemini-.*`` and
      routes it to its built-in Gemini backend itself, so there's nothing to wrap.
    * anything else -> a ``LiteLlm`` adapter. ADK has no native backend for these, so the call
      goes through LiteLLM, which picks the right SDK/key from a ``"<provider>/<model>"`` id.
    """
    prov = provider.lower()

    # Gemini is ADK-native: the registry resolves the bare string; the provider is implicit.
    if prov == "gemini":
        return model

    # No native ADK backend for this provider -> hand it to the LiteLLM adapter. LiteLLM needs
    # the provider baked into the model id ("<provider>/<model>") to choose the SDK + env key.
    lite_id = model if "/" in model else f"{prov}/{model}"
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # litellm is the optional 'llm' dep group
        raise RuntimeError(
            f"model {model!r} (provider {prov!r}) needs litellm — run: uv sync --group llm"
        ) from exc
    return LiteLlm(model=lite_id)

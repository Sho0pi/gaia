"""Resolve a model id to something ADK's ``LlmAgent`` accepts.

ADK natively handles only Gemini model strings (``gemini-.*``). Every other provider runs
through ADK's LiteLLM wrapper. :func:`resolve_model` keeps Gemini as a bare string (no
behaviour change) and wraps anything else in a ``LiteLlm`` — so `llm: { provider: openai,
model: gpt-4o }` in ``gaia.yaml`` just works once ``OPENAI_API_KEY`` is in the env.

litellm is an optional dependency (the ``llm`` group); it's imported lazily and only when a
non-Gemini model is actually configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.models.base_llm import BaseLlm
    from google.adk.planners import BuiltInPlanner

#: Gemini takes a thinking *budget* (tokens), not an effort level, so map our levels to one.
#: ``minimal`` keeps a sliver of thinking; the rest climb. (Gemini 3 prefers thinking_level,
#: but a budget still works; revisit if we add a 3-specific path.)
_THINKING_BUDGET = {"minimal": 512, "low": 1024, "medium": 8192, "high": 24576, "max": 24576}


def resolve_model(
    model: str, provider: str, *, use_oauth: bool = False, effort: str = ""
) -> str | BaseLlm:
    """Build the model object ADK's ``LlmAgent(model=...)`` needs for ``provider``/``model``.

    The return type differs by provider because ADK only has a *native* backend for one of
    them — Gemini. ``provider`` and ``model`` are both required and taken verbatim from
    ``llm.provider`` / ``llm.model`` (no inference).

    * ``gemini`` -> the bare model **string**. ADK's model registry matches ``gemini-.*`` and
      routes it to its built-in Gemini backend itself, so there's nothing to wrap. Reasoning
      effort for Gemini is applied at the agent level (see :func:`thinking_planner`).
    * ``openai`` with ``use_oauth`` -> our ChatGPT subscription backend (Sign in with ChatGPT).
    * anything else -> a ``LiteLlm`` adapter. ADK has no native backend for these, so the call
      goes through LiteLLM, which picks the right SDK/key from a ``"<provider>/<model>"`` id.

    ``effort`` (minimal|low|medium|high, blank = provider default) sets the reasoning level: the
    LiteLLM path passes it as ``reasoning_effort`` (LiteLLM maps it per provider — OpenAI
    passthrough, Anthropic ``output_config.effort``); the OAuth backend carries it into its
    Responses ``reasoning.effort``.
    """
    prov = provider.lower()

    # Gemini is ADK-native: the registry resolves the bare string; the provider is implicit.
    if prov == "gemini":
        return model

    # OpenAI has two auth modes: an API key (default, via LiteLLM) or a ChatGPT subscription
    # (use_oauth). The OAuth tokens hit chatgpt.com/backend-api, which LiteLLM can't speak, so
    # that path uses our own ADK backend. ('openai-chatgpt'/'chatgpt' imply OAuth.)
    if prov in ("openai-chatgpt", "chatgpt") or (prov == "openai" and use_oauth):
        from gaia.providers.openai import ChatGptOAuthLlm

        return ChatGptOAuthLlm(model=model, effort=effort)

    # No native ADK backend for this provider -> hand it to the LiteLLM adapter. LiteLLM needs
    # the provider baked into the model id ("<provider>/<model>") to choose the SDK + env key.
    # OpenRouter model ids already contain a "/" (e.g. "anthropic/claude-..."), so the generic
    # "/" check would wrongly skip the prefix and route straight to that vendor — force it.
    if prov == "openrouter":
        lite_id = model if model.startswith("openrouter/") else f"openrouter/{model}"
    else:
        lite_id = model if "/" in model else f"{prov}/{model}"
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # litellm is the optional 'llm' dep group
        raise RuntimeError(
            f"model {model!r} (provider {prov!r}) needs litellm — run: uv sync --group llm"
        ) from exc
    # LiteLlm forwards extra kwargs to litellm.completion; reasoning_effort is the unified knob.
    extra = {"reasoning_effort": effort} if effort else {}
    return LiteLlm(model=lite_id, **extra)


def _gemini_thinks(model: str) -> bool:
    """Whether a Gemini model has a tunable thinking budget (2.5+ / 3); 2.0 and older don't."""
    return "2.5" in model or "-3" in model or "3.0" in model


def thinking_planner(provider: str, model: str, effort: str) -> BuiltInPlanner | None:
    """An ADK planner that sets Gemini's thinking budget for ``effort``, or ``None``.

    Gemini effort can't ride on the model string (it's a bare string ADK resolves natively),
    so it's applied as an agent ``planner``. Non-Gemini providers carry effort on the model
    object itself (see :func:`resolve_model`) and need no planner. Returns ``None`` when there's
    nothing to do (no effort, non-Gemini, or a non-thinking Gemini like gemini-2.0-flash).
    """
    if not effort or provider.lower() != "gemini" or not _gemini_thinks(model):
        return None
    budget = _THINKING_BUDGET.get(effort.lower())
    if budget is None:
        return None
    from google.adk.planners import BuiltInPlanner
    from google.genai import types

    return BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_budget=budget))

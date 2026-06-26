"""Fetch the chat models a provider actually offers, for the setup picker.

Best-effort + lazy (heavy SDK imports inside): returns ``[]`` on any failure so the caller falls
back to a curated list. Only the API-key providers have a list endpoint — the ChatGPT oauth/Codex
backend doesn't, so it returns ``[]`` (→ curated).
"""

from __future__ import annotations

#: Substrings that mark a NON-chat OpenAI model (embeddings, audio, image, …) — filtered out.
_OPENAI_DROP = (
    "embedding",
    "whisper",
    "tts",
    "audio",
    "image",
    "dall-e",
    "moderation",
    "realtime",
    "transcribe",
    "search",
    "computer-use",
)
#: Prefixes that mark an OpenAI chat/reasoning model.
_OPENAI_KEEP = ("gpt-", "o1", "o3", "o4", "chatgpt")


def available_models(provider: str, *, api_key: str | None, use_oauth: bool) -> list[str]:
    """Chat model ids for ``provider`` (newest first), or ``[]`` to signal "use the fallback"."""
    if use_oauth:
        return []  # Codex/oauth backend has no public /models endpoint
    try:
        if provider == "openai" and api_key:
            return _openai_models(api_key)
        if provider == "gemini" and api_key:
            return _gemini_models(api_key)
    except Exception:
        return []  # network down, bad key, SDK change — caller falls back to curated
    return []


def _openai_models(api_key: str) -> list[str]:
    import openai

    models = openai.OpenAI(api_key=api_key, timeout=10).models.list()
    rows = [(getattr(m, "created", 0), m.id) for m in models if _is_openai_chat(m.id)]
    return [mid for _created, mid in sorted(rows, reverse=True)]  # newest first


def _is_openai_chat(model_id: str) -> bool:
    low = model_id.lower()
    return low.startswith(_OPENAI_KEEP) and not any(bad in low for bad in _OPENAI_DROP)


def _gemini_models(api_key: str) -> list[str]:
    from google import genai

    out: list[str] = []
    for m in genai.Client(api_key=api_key).models.list():
        actions = getattr(m, "supported_actions", None) or []
        if "generateContent" in actions:
            out.append(str(getattr(m, "name", "")).removeprefix("models/"))
    return [m for m in out if m]

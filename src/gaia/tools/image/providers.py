"""Image-generation backends: gemini (Imagen) and openai (gpt-image-1).

Pluggable like the browser/search backends. Each provider is a sync SDK call wrapped in a
thread (the SDKs read their key from the env — ``GOOGLE_API_KEY`` / ``OPENAI_API_KEY`` —
which :func:`gaia.config.configure_adk_env` exports). Returns raw PNG bytes; the tool saves
them. SDKs imported lazily (heavy-deps convention).
"""

from __future__ import annotations

import asyncio

#: Default model per provider when ``tools.generate_image.model`` isn't set.
DEFAULT_MODELS = {"gemini": "imagen-3.0-generate-002", "openai": "gpt-image-1"}

#: Aspect ratio -> openai size (gpt-image-1 takes a pixel size, not a ratio).
_OPENAI_SIZES = {"1:1": "1024x1024", "3:4": "1024x1536", "4:3": "1536x1024", "16:9": "1536x1024"}


async def generate_images(
    provider: str, prompt: str, *, aspect_ratio: str = "1:1", n: int = 1, model: str = ""
) -> list[bytes]:
    """Generate ``n`` images for ``prompt`` via ``provider``; return PNG byte blobs."""
    model = model or DEFAULT_MODELS.get(provider, "")
    if provider == "gemini":
        return await asyncio.to_thread(_gemini, prompt, aspect_ratio, n, model)
    if provider == "openai":
        return await asyncio.to_thread(_openai, prompt, aspect_ratio, n, model)
    raise ValueError(f"unknown image provider {provider!r} (try: {', '.join(DEFAULT_MODELS)})")


def _gemini(prompt: str, aspect_ratio: str, n: int, model: str) -> list[bytes]:
    from google import genai
    from google.genai import types

    client = genai.Client()  # reads GOOGLE_API_KEY from env
    resp = client.models.generate_images(
        model=model,
        prompt=prompt,
        config=types.GenerateImagesConfig(number_of_images=n, aspect_ratio=aspect_ratio),
    )
    return [
        img.image.image_bytes
        for img in (resp.generated_images or [])
        if img.image and img.image.image_bytes
    ]


def _openai(prompt: str, aspect_ratio: str, n: int, model: str) -> list[bytes]:
    import base64

    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY from env
    resp = client.images.generate(
        model=model, prompt=prompt, n=n, size=_OPENAI_SIZES.get(aspect_ratio, "1024x1024")
    )
    return [base64.b64decode(d.b64_json) for d in (resp.data or []) if d.b64_json]

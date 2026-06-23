"""Image-generation backends: gemini (Imagen), openai (gpt-image-1), cloudflare (SDXL worker).

Pluggable like the browser/search backends. The SDK backends are a sync call wrapped in a thread
(the SDKs read their key from the env — ``GOOGLE_API_KEY`` / ``OPENAI_API_KEY`` — which
:func:`gaia.config.configure_adk_env` exports). The cloudflare backend is a plain async HTTP POST to
the user's own Worker (token from ``CLOUDFLARE_AI_TOKEN``, url + SDXL knobs in ``options``). Each
returns raw image bytes; the tool saves them. SDKs imported lazily (heavy-deps convention).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

#: Default model per provider when ``tools.generate_image.model`` isn't set. (cloudflare's model is
#: fixed by the Worker, so this is informational only.)
DEFAULT_MODELS = {
    "gemini": "imagen-3.0-generate-002",
    "openai": "gpt-image-1",
    "cloudflare": "@cf/stabilityai/stable-diffusion-xl-base-1.0",
}

#: Aspect ratio -> openai size (gpt-image-1 takes a pixel size, not a ratio).
_OPENAI_SIZES = {"1:1": "1024x1024", "3:4": "1024x1536", "4:3": "1536x1024", "16:9": "1536x1024"}

#: Aspect ratio -> (width, height) for SDXL (it has no aspect field; all within its px range).
_ASPECT_WH = {"1:1": (1024, 1024), "3:4": (896, 1152), "4:3": (1152, 896), "16:9": (1280, 720)}


async def generate_images(
    provider: str,
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    n: int = 1,
    model: str = "",
    negative_prompt: str = "",
    options: dict[str, Any] | None = None,
) -> list[bytes]:
    """Generate ``n`` images for ``prompt`` via ``provider``; return image byte blobs.

    ``negative_prompt`` and ``options`` (backend-specific config) are used by ``cloudflare``; the
    SDK backends ignore them.
    """
    model = model or DEFAULT_MODELS.get(provider, "")
    if provider == "gemini":
        return await asyncio.to_thread(_gemini, prompt, aspect_ratio, n, model)
    if provider == "openai":
        return await asyncio.to_thread(_openai, prompt, aspect_ratio, n, model)
    if provider == "cloudflare":
        return await _cloudflare(prompt, aspect_ratio, negative_prompt, options or {})
    raise ValueError(f"unknown image provider {provider!r} (try: {', '.join(DEFAULT_MODELS)})")


async def _cloudflare(
    prompt: str, aspect_ratio: str, negative_prompt: str, options: dict[str, Any]
) -> list[bytes]:
    """POST the prompt to the user's Cloudflare AI Worker (SDXL); return its jpeg bytes."""
    import httpx

    token = os.environ.get("CLOUDFLARE_AI_TOKEN")
    if not token:
        raise ValueError("set GAIA_CLOUDFLARE_AI_TOKEN to use the cloudflare image backend")
    url = options.get("url")
    if not url:
        raise ValueError("set tools.generate_image.cloudflare_url to the Worker URL")

    width, height = _ASPECT_WH.get(aspect_ratio, _ASPECT_WH["1:1"])
    body: dict[str, Any] = {
        "prompt": prompt,
        "width": options.get("width", width),
        "height": options.get("height", height),
    }
    if negative_prompt:
        body["negative_prompt"] = negative_prompt
    for key in ("num_steps", "guidance", "seed"):  # forwarded only when set in config
        if options.get(key) is not None:
            body[key] = options[key]

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(str(url), headers={"Authorization": f"Bearer {token}"}, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"cloudflare worker {resp.status_code}: {resp.text[:300]}")
    return [resp.content]


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

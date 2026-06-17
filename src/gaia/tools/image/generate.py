"""The ``generate_image`` tool: render an image from a prompt and save it to the workspace.

A skill can describe *how* to prompt for an image, but only a tool can produce one. This
renders the prompt via the configured backend (:mod:`gaia.tools.image.providers`), writes
the PNG into the calling agent's workspace, and returns its path — which the handler turns
into a real image reply (see :mod:`gaia.core.screenshots`, same path as screenshots).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools.fs.base import sandbox_for

NAME = "generate_image"


def make_generate_image(
    provider: str = "gemini", model: str = ""
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ADK ``generate_image`` tool bound to a backend ``provider``/``model``."""

    async def generate_image(
        prompt: str, aspect_ratio: str = "1:1", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Generate an image from a text prompt and show it to the user.

        Describe the image you want in detail. The picture is created and sent to the user
        automatically — just report what you made.

        Args:
            prompt: a detailed description of the image to create.
            aspect_ratio: one of 1:1, 3:4, 4:3, 16:9 (default 1:1).
        """
        from gaia.tools.image.providers import generate_images

        if not prompt.strip():
            return {"status": "error", "error_message": "prompt must not be empty"}
        try:
            images = await generate_images(provider, prompt, aspect_ratio=aspect_ratio, model=model)
        except Exception as exc:  # tools never raise to the model
            return {"status": "error", "error_message": f"image generation failed: {exc}"}
        if not images:
            return {"status": "error", "error_message": "the model returned no image"}

        # Land the PNG in the calling agent's own workspace (same dir screenshots use).
        workspace = sandbox_for(constants.AGENTS_DIR, tool_context.agent_name).primary
        target = workspace / f"image-{int(time.time() * 1000)}.png"
        target.write_bytes(images[0])
        return {"status": "success", "path": str(target), "prompt": prompt}

    return generate_image

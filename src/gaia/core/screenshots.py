"""Turn a finished turn's screenshot tool results into :class:`Media` replies.

The model only streams text; a screenshot tool writes a PNG and reports it in its tool
result. This module scans a turn's ADK function responses for those files and yields a
:class:`~gaia.connectors.base.Media` per screenshot, so a connector that supports
images (WhatsApp) delivers the actual picture instead of just a path. Kept out of
``handler.py`` so the handler stays the thin text↔Runner glue and the (chunkier)
backend-specific extraction lives on its own.

Both browser backends are handled: the native ``browser_screenshot`` (returns a
``{"status": "success", "path": ...}`` dict) and playwright-mcp's
``browser_take_screenshot`` (returns an MCP ``CallToolResult`` dict of content blocks).
"""

from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.connectors.base import Media

#: playwright-mcp's screenshot tool (the mcp browser backend). Its result is an MCP
#: ``CallToolResult`` dict (content blocks), not the native tool's ``{"path": ...}``.
_MCP_SCREENSHOT = "browser_take_screenshot"
#: Matches a saved image filename/path inside playwright-mcp's text response. It reports
#: the file as a markdown link, e.g. ``[Screenshot of viewport](./flow.png)`` — the char
#: class excludes ``[](`` so the match is just the path token.
_IMAGE_PATH_RE = re.compile(r"[\w./\\-]+\.(?:png|jpe?g)", re.IGNORECASE)


def media_for_screenshots(events: list[Any]) -> list[Media]:
    """Every screenshot taken in ``events``, as :class:`Media` replies (in order).

    Only screenshots Gaia itself takes are seen here — files a delegated soul produces
    come back via delegate_to_soul and are a follow-up.
    """
    media: list[Media] = []
    for event in events:
        get_responses = getattr(event, "get_function_responses", None)
        if get_responses is None:
            continue
        for resp in get_responses() or []:
            one = _screenshot_media(resp.name, resp.response)
            if one is not None:
                media.append(one)
    return media


def _screenshot_media(name: str, result: Any) -> Media | None:
    """A :class:`Media` reply for a screenshot tool result, or ``None`` if it isn't one."""
    from gaia.connectors.base import Media
    from gaia.tools.browser import SCREENSHOT
    from gaia.tools.image import NAME as GENERATE_IMAGE

    if not isinstance(result, dict):
        return None
    if name == SCREENSHOT and result.get("status") == "success" and result.get("path"):
        return Media(Path(result["path"]), caption="screenshot")
    if name == GENERATE_IMAGE and result.get("status") == "success" and result.get("path"):
        return Media(Path(result["path"]), caption="image")
    if name == _MCP_SCREENSHOT and not result.get("isError"):
        path = _mcp_screenshot_path(result)
        if path is not None:
            return Media(path, caption="screenshot")
    return None


def _mcp_screenshot_path(result: dict[str, Any]) -> Path | None:
    """Extract the saved image file from a playwright-mcp screenshot result.

    Prefers the file named in a text block. playwright-mcp writes the screenshot relative
    to its process cwd (which we pin to :func:`gaia.mcp.browser_output_dir`), reporting it
    as a markdown link like ``[Screenshot of viewport](./flow.png)``; we resolve the
    token's basename against that workspace. Falls back to decoding an inline base64 image
    block into the workspace so we still deliver the picture if no path is reported.
    """
    from gaia.mcp import browser_output_dir

    content = result.get("content")
    if not isinstance(content, list):
        return None
    out = browser_output_dir()
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            for token in _IMAGE_PATH_RE.findall(str(item.get("text", ""))):
                # The reported path is relative to the server's cwd; resolve its basename
                # against the workspace, then accept an absolute path as a fallback.
                resolved = out / Path(token).name
                if resolved.is_file():
                    return resolved
                absolute = Path(token)
                if absolute.is_absolute() and absolute.is_file():
                    return absolute
    for item in content:
        if isinstance(item, dict) and item.get("type") == "image" and item.get("data"):
            from gaia.mcp import browser_output_dir

            try:
                blob = base64.b64decode(item["data"])
            except (ValueError, TypeError):
                continue
            out = browser_output_dir()
            out.mkdir(parents=True, exist_ok=True)
            target = out / f"screenshot-{int(time.time() * 1000)}.png"
            target.write_bytes(blob)
            return target
    return None

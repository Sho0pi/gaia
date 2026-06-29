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


def media_for_outputs(events: list[Any]) -> list[Media]:
    """Every file a turn produced for the user, as :class:`Media` replies (in order).

    Covers screenshots Gaia takes itself, any file it explicitly sends with ``send_file``
    (a generated image, a soul's deliverable, an uploaded file), and the media a delegated
    soul produced — those ride back in the ``delegate_to_soul`` result and are delivered here,
    so the root needn't re-serve/re-screenshot to show them.
    """
    media: list[Media] = []
    for event in events:
        get_responses = getattr(event, "get_function_responses", None)
        if get_responses is None:
            continue
        for resp in get_responses() or []:
            media.extend(_output_media(resp.name, resp.response))
    return media


def _output_media(name: str, result: Any) -> list[Media]:
    """The :class:`Media` replies for one tool result (screenshot / send_file / delegate)."""
    from gaia.connectors.base import Media, media_kind
    from gaia.souls.delegate import NAME as DELEGATE
    from gaia.tools.browser import SCREENSHOT
    from gaia.tools.download_media import NAME as DOWNLOAD_MEDIA
    from gaia.tools.image import NAME as GENERATE_IMAGE
    from gaia.tools.send_file import NAME as SEND_FILE

    if not isinstance(result, dict):
        return []
    if name == SEND_FILE and result.get("status") == "success" and result.get("path"):
        # The model picked the file and its caption; kind was inferred by the tool.
        return [
            Media(
                Path(result["path"]), caption=result.get("caption", ""), kind=result.get("kind", "")
            )
        ]
    if name == SCREENSHOT and result.get("status") == "success" and result.get("path"):
        return [Media(Path(result["path"]), caption="screenshot")]
    if name == GENERATE_IMAGE and result.get("status") == "success" and result.get("path"):
        return [Media(Path(result["path"]), caption="image")]
    if name == DOWNLOAD_MEDIA and result.get("status") == "success" and result.get("path"):
        media_path = Path(result["path"])
        return [Media(media_path, kind=media_kind(media_path))]  # kind from the file (video/audio)
    if name == _MCP_SCREENSHOT and not result.get("isError"):
        path = _mcp_screenshot_path(result)
        return [Media(path, caption="screenshot")] if path is not None else []
    if name == DELEGATE and result.get("status") == "success":
        # A soul's deliverable media comes back through delegate_to_soul; deliver each here so
        # the root needn't re-serve/re-screenshot to show it. Kind is re-inferred from the file.
        return [Media(p, kind=media_kind(p)) for p in map(Path, result.get("media") or [])]
    return []


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

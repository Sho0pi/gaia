"""``download_media`` — download a video/audio from a link with yt-dlp, into the agent's workspace.

Opt-in: needs the ``media`` extra (``pip install 'gaia[media]'`` → yt-dlp). Registry attaches it
tool only when ``yt_dlp`` is importable. The downloaded file rides out via
:func:`gaia.core.screenshots.media_for_outputs` (auto-delivered, like a screenshot), so the model
doesn't also ``send_file`` it.

yt-dlp is blocking, so the actual download runs in a worker thread. The input URL goes through the
same SSRF guard as ``web_fetch`` (``validate_url``); yt-dlp's own CDN fetches are not re-checked (a
known, accepted gap for now).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools._helpers import err, ok

#: Tool id (matches the closure name); recognised by the outbound-media event scan.
NAME = "download_media"

#: Skip anything larger than this — keeps a runaway link from filling the disk / chat.
_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


def make_download_media() -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ``download_media`` tool (yt-dlp-backed)."""

    async def download_media(url: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Download a video or audio clip from a link and send it to the user.

        Use this for media the user asks for from a public link (an Instagram reel, a TikTok, a
        YouTube clip, a Twitter video). It fetches with yt-dlp and the file is delivered to the user
        AUTOMATICALLY — do NOT also send_file it. Not for paywalled / DRM / login-required content.

        Args:
            url: the page or share link of the video/audio to download.
        """
        from gaia.tools.fs.base import sandbox_for
        from gaia.tools.web_fetch import validate_url

        cleaned = url.strip()
        if not cleaned:
            return err("url must not be empty")
        error = validate_url(cleaned)  # reuse the web SSRF guard on the input link
        if error is not None:
            return err(error)

        workspace = sandbox_for(constants.AGENTS_DIR, tool_context.agent_name).primary
        try:
            path, title = await asyncio.to_thread(_download, cleaned, workspace)
        except Exception as exc:
            return err(f"download failed: {exc}")
        return ok(path=str(path), title=title)

    return download_media


def _download(url: str, workspace: Path) -> tuple[Path, str]:
    """Blocking yt-dlp download into ``workspace``; returns ``(file, title)``. Lazy yt_dlp."""
    import yt_dlp

    opts = {
        "outtmpl": str(workspace / "%(title).80s-%(id)s.%(ext)s"),
        "noplaylist": True,  # a share link, not a channel/playlist
        "max_filesize": _MAX_BYTES,
        "quiet": True,
        "no_warnings": True,
        # Prefer a single progressive mp4 so no ffmpeg merge is needed (reels are single-file).
        "format": "best[ext=mp4]/best",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        title = str(info.get("title") or path.stem)
    if not path.is_file():
        raise FileNotFoundError(
            "no file produced (over the 100MB cap, or the link isn't supported)"
        )
    return path, title

"""``download_media`` — download a video/audio from a link, into the agent's workspace.

Opt-in: needs the ``media`` extra (``pip install 'gaia[media]'`` → yt-dlp). Attached only
when ``yt_dlp`` is importable. The downloaded file rides out via
:func:`gaia.core.screenshots.media_for_outputs` (auto-delivered, like a screenshot), so the model
doesn't also ``send_file`` it.

Two stages, so the model just calls ``download_media(url)``:

* **yt-dlp** for open sites (YouTube, TikTok, Twitter, …). Blocking → runs in a worker thread.
* **Downloader-site fallback** when yt-dlp can't (Instagram reels need a logged-in session, which we
  refuse to fake from a flagged IP). We drive a public downloader (``fastvideosave.net``) in a
  browser and *network-capture* the call its page makes to ``api.videodropper.app/allinone`` (its JS
  encrypts the reel URL into a header), read the resolved Instagram CDN mp4 from the response,
  then download it. Frictionless (no login/cookies) but fragile: if that site changes, this breaks.

The input URL (and the resolved CDN URL) go through the same SSRF guard as ``web_fetch``.
"""

from __future__ import annotations

import asyncio
import time
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

#: The public downloader used by the fallback, and the backend endpoint its page calls.
_DOWNLOADER_SITE = "https://fastvideosave.net/"
_DOWNLOADER_API = "videodropper.app/allinone"


def make_download_media(browser_cfg: Any = None) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ``download_media`` tool. ``browser_cfg`` powers the downloader-site fallback."""

    async def download_media(url: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Download a video or audio clip from a link and send it to the user.

        Use this for media the user asks for from a public link — an Instagram reel, a TikTok, a
        YouTube clip, a Twitter video. The file is delivered to the user AUTOMATICALLY — do NOT also
        send_file it. Reels work too (handled via a downloader site). Not for paywalled / DRM /
        purchased content.

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

        # 1) yt-dlp — handles open sites directly.
        try:
            path, title = await asyncio.to_thread(_download, cleaned, workspace)
            return ok(path=str(path), title=title)
        except Exception as yt_err:
            # 2) Fallback: a public downloader site (Instagram etc. yt-dlp can't do anonymously).
            try:
                path = await _resolve_via_downloader(cleaned, workspace, browser_cfg)
                return ok(path=str(path), title=path.stem)
            except Exception as site_err:
                return err(f"download failed (yt-dlp: {yt_err}; downloader: {site_err})")

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


async def _resolve_via_downloader(url: str, workspace: Path, browser_cfg: Any) -> Path:
    """Drive a public downloader site in the browser, capture the resolved CDN mp4, download it.

    We let the site's page make its (header-encrypted) backend call and read the response, rather
    than reproducing its client-side crypto. Fragile by nature — a clear error if it stops working.
    """
    from gaia.tools.browser.base import make_launcher
    from gaia.tools.web_fetch import validate_url

    page, close = await make_launcher(browser_cfg)()
    captured: list[str] = []

    async def on_response(resp: Any) -> None:
        if _DOWNLOADER_API in resp.url:
            try:
                data = await resp.json()
                media = (data.get("video") or [{}])[0].get("video")
            except Exception:
                return
            if media:
                captured.append(str(media))

    page.on("response", on_response)
    try:
        await page.goto(_DOWNLOADER_SITE, wait_until="domcontentloaded", timeout=40000)
        await page.fill("#form input", url)
        try:
            await page.click("#form button", timeout=8000)
        except Exception:
            await page.press("#form input", "Enter")
        for _ in range(30):  # up to ~15s for the page's resolver call to land
            if captured:
                break
            await asyncio.sleep(0.5)
    finally:
        await close()

    if not captured:
        raise RuntimeError(
            "the downloader site returned no media (it likely changed — needs a fix)"
        )
    media_url = captured[0]
    if validate_url(media_url) is not None:  # SSRF-guard the resolved URL too
        raise RuntimeError("resolved media URL is not allowed")
    return await asyncio.to_thread(_fetch_file, media_url, workspace)


def _fetch_file(media_url: str, workspace: Path) -> Path:
    """Download ``media_url`` (a CDN mp4) into ``workspace`` with a browser-like Referer.

    Redirects are followed manually (``follow_redirects=False``) so the SSRF guard
    (``validate_url``) runs on **every** hop — a CDN 302 to an internal address can't slip
    through, the same protection ``web_fetch`` maintains."""
    import httpx

    from gaia.tools.web_fetch import MAX_REDIRECTS, _resolve_location, validate_url

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"}
    current = media_url
    data = b""
    with httpx.Client(follow_redirects=False, timeout=60.0, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            error = validate_url(current)  # re-check each hop, not just the first
            if error is not None:
                raise ValueError(f"blocked media URL: {error}")
            with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError("redirect without a location")
                    current = _resolve_location(current, location)
                    continue
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    data += chunk
                    if len(data) > _MAX_BYTES:
                        raise ValueError("media is over the 100MB cap")
                break
        else:
            raise ValueError("too many redirects")
    if len(data) < 1024:
        raise ValueError("resolved media was empty")
    target = workspace / f"video-{int(time.time() * 1000)}.mp4"
    target.write_bytes(data)
    return target

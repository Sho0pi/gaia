"""Headless render of a local HTML file to a PNG — for previewing a built deliverable.

When a mission produces a website (``index.html`` + css/js in a soul's workspace), the
delivery step renders it to an image so the user *sees* the page on WhatsApp instead of raw
source. This is a direct, agent-free render of our own trusted local file, so it bypasses the
``browser_navigate`` SSRF guard (which deliberately blocks ``file://``).

Playwright is the optional ``browser`` dependency group, imported lazily; when it's absent (or
the render fails) the caller falls back to delivering the text/source, so nothing is lost.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

logger = logging.getLogger(__name__)


async def render_html_to_png(html_path: Path, out_png: Path) -> Path | None:
    """Render ``html_path`` in a headless browser and save a full-page PNG at ``out_png``.

    Returns ``out_png`` on success, or ``None`` when Playwright isn't installed or anything
    goes wrong (logged) — the caller then falls back to a non-rendered delivery.
    """
    if importlib.util.find_spec("playwright") is None:
        logger.info("preview render skipped: Playwright not installed (the 'browser' group)")
        return None
    if not html_path.is_file():  # noqa: ASYNC240 - trivial local stat, not real I/O blocking
        return None

    from gaia.tools.browser.base import _playwright_launcher

    uri = html_path.resolve().as_uri()  # noqa: ASYNC240 - trivial path op, not blocking I/O
    page, close = await _playwright_launcher()
    try:
        await page.goto(uri)
        await page.screenshot(path=str(out_png), full_page=True)
        return out_png
    except Exception:  # pragma: no cover - best-effort; fall back to text delivery
        logger.warning("preview render failed for %s", html_path, exc_info=True)
        return None
    finally:
        await close()

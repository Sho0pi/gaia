"""System test: render a local HTML file to a PNG with real headless Chromium.

Gated on the optional 'browser' dep (Playwright + its Chromium build) so CI stays green
without it. No network (a ``file://`` fixture), no model, no tokens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api", reason="needs the optional 'browser' dep group")

from gaia.tools.browser.render import render_html_to_png

pytestmark = pytest.mark.system


async def test_render_html_to_png_produces_an_image(tmp_path: Path) -> None:
    html = tmp_path / "index.html"
    html.write_text(
        "<html><body style='background:#0a0'><h1>Gym Site</h1><p>A/B split</p></body></html>"
    )
    out = tmp_path / "_preview.png"

    result = await render_html_to_png(html, out)

    assert result == out
    assert out.exists() and out.stat().st_size > 1000  # a real rendered page, not empty
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic


async def test_render_missing_file_returns_none(tmp_path: Path) -> None:
    assert await render_html_to_png(tmp_path / "nope.html", tmp_path / "o.png") is None

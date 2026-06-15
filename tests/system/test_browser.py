"""System test: the browser tools drive a real headless Chromium end to end.

Gated twice so CI stays green without the optional dep: skip if Playwright isn't
installed, and skip if its Chromium build is missing (``playwright install chromium``).
Loads a local ``file://`` fixture — no network — then asserts the session closes with
no orphan browser left behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright.async_api", reason="needs the optional 'browser' dep group")

from gaia.tools import browser
from gaia.tools.browser.base import BrowserSessionManager

pytestmark = pytest.mark.system


class _Ctx:
    agent_name = "sys-browser"


_FIXTURE = "<html><title>Fixture</title><body><button>Press me</button></body></html>"


async def _chromium_available() -> bool:
    from playwright.async_api import async_playwright

    try:
        pw = await async_playwright().start()
        try:
            browser_ = await pw.chromium.launch(headless=True)
            await browser_.close()
        finally:
            await pw.stop()
        return True
    except Exception:
        return False


async def test_navigate_snapshot_screenshot_then_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not await _chromium_available():
        pytest.skip("chromium not installed (run: uv run playwright install chromium)")

    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    page_file = tmp_path / "fixture.html"
    page_file.write_text(_FIXTURE)
    file_url = page_file.as_uri()

    manager = BrowserSessionManager()
    navigate = browser.make_browser_navigate(manager)
    snapshot = browser.make_browser_snapshot(manager)
    screenshot = browser.make_browser_screenshot(manager)
    ctx: Any = _Ctx()

    try:
        # browser_navigate allows file:// only under AGENTS_DIR; this fixture is elsewhere,
        # so it's refused by design — assert that, then seed the real page directly to
        # exercise the live-Chromium snapshot/screenshot path without touching the net.
        refused = await navigate(file_url, tool_context=ctx)
        assert refused["status"] == "error"
        assert "agents workspace" in refused["error_message"]

        session = await manager.get(ctx.agent_name)
        await session.page.goto(file_url)

        snap = await snapshot(tool_context=ctx)
        assert snap["status"] == "success"
        assert "button" in snap["snapshot"].lower()

        shot = await screenshot(tool_context=ctx)
        assert shot["status"] == "success"
        assert Path(shot["path"]).is_file()  # noqa: ASYNC240 - assertion, not hot-path I/O
    finally:
        await manager.close_all()

    # After close_all the session is gone — a fresh get would launch a new browser.
    assert manager._sessions == {}

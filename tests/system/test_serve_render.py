"""System test: serving over http renders a site that file:// can't, and navigate trusts it.

The point of the serve tools: a real site (root-absolute assets, module scripts) renders
blank under ``file://`` but correctly over ``http://127.0.0.1:<port>``. Here we serve a
fixture whose body is written by a ``/main.js`` module referenced with a root-absolute path
— exactly what breaks on file:// — and assert the live browser sees the rendered text.

Gated on the optional 'browser' dep + an installed Chromium, like test_browser.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright.async_api", reason="needs the optional 'browser' dep group")

from gaia.tools import browser
from gaia.tools.browser.base import BrowserSessionManager
from gaia.tools.serve import ServedPorts, StaticServerManager

pytestmark = pytest.mark.system

# Body is empty in HTML; a root-absolute module script fills it. Under file:// the module
# is CORS-blocked and "/main.js" resolves to the fs root → blank. Over http it works.
_INDEX = "<html><head><script type=module src=/main.js></script></head><body></body></html>"
_MAIN = "document.body.textContent = 'RENDERED_BY_MODULE'"


class _Ctx:
    agent_name = "sys-serve"


async def _chromium_available() -> bool:
    from playwright.async_api import async_playwright

    try:
        pw = await async_playwright().start()
        try:
            b = await pw.chromium.launch(headless=True)
            await b.close()
        finally:
            await pw.stop()
        return True
    except Exception:
        return False


async def test_served_site_renders_and_navigate_trusts_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not await _chromium_available():
        pytest.skip("chromium not installed (run: uv run playwright install chromium)")

    agents = tmp_path / "agents"
    ws = agents / "designer" / "workspace"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text(_INDEX)
    (ws / "main.js").write_text(_MAIN)
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", agents)
    monkeypatch.setattr("gaia.tools.serve.base.constants.AGENTS_DIR", agents)

    served = ServedPorts()
    server = StaticServerManager(served, idle_seconds=999)
    manager = BrowserSessionManager()
    navigate = browser.make_browser_navigate(manager, served)
    snapshot = browser.make_browser_snapshot(manager)
    ctx: Any = _Ctx()

    try:
        site, url = await server.serve(str(ws))
        assert site.port in served

        # navigate (with the SSRF guard) accepts our served loopback port.
        nav = await navigate(url, tool_context=ctx)
        assert nav["status"] == "success", nav

        snap = await snapshot(tool_context=ctx)
        # The module ran (http origin) and filled the body — file:// would be blank.
        assert "RENDERED_BY_MODULE" in snap.get("snapshot", "")
    finally:
        await manager.close_all()
        await server.close_all()

"""System test: a real pinggy tunnel exposes a served site on a public https URL.

Opens an actual public tunnel (ssh → free.pinggy.io) and hits it over the internet, so it
is gated behind GAIA_TUNNEL_SYSTEST=1 — never runs in CI (needs outbound + ssh + is flaky).
"""

from __future__ import annotations

import asyncio
import os
import urllib.request
from pathlib import Path

import pytest

from gaia.tools.serve import ServedPorts, StaticServerManager, TunnelManager

pytestmark = pytest.mark.system

if os.environ.get("GAIA_TUNNEL_SYSTEST") != "1":
    pytest.skip(
        "set GAIA_TUNNEL_SYSTEST=1 to run the live pinggy tunnel test", allow_module_level=True
    )


async def test_pinggy_tunnel_serves_public_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "agents"
    ws = agents / "designer" / "workspace"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text("<h1>PUBLIC_OK</h1>")
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", agents)
    monkeypatch.setattr("gaia.tools.serve.base.constants.AGENTS_DIR", agents)

    server = StaticServerManager(ServedPorts(), idle_seconds=999)
    tunnel = TunnelManager(provider="pinggy", timeout_seconds=30)
    try:
        site, _ = await server.serve(str(ws))
        url = await tunnel.open(site.port)
        assert url.startswith("https://")
        # pinggy free shows an interstitial on first hit; retry a few times for the body.
        body = ""
        for _ in range(5):
            body = await asyncio.to_thread(
                lambda: urllib.request.urlopen(url + "/index.html", timeout=15).read().decode()
            )
            if "PUBLIC_OK" in body:
                break
            await asyncio.sleep(2)
        assert "PUBLIC_OK" in body
    finally:
        await tunnel.close_all()
        await server.close_all()

"""serve / serve_stop / serve_list tools + StaticServerManager confinement and lifecycle."""

from __future__ import annotations

import asyncio
import urllib.request
from pathlib import Path

import pytest

from gaia.tools.serve import ServedPorts, ServeError, StaticServerManager, make_serve
from gaia.tools.serve.base import _resolve_under_agents


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    agents = tmp_path / "agents"
    ws = agents / "demo" / "workspace"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text("<h1>hi</h1>")
    # Point AGENTS_DIR at the temp tree for both the manager and the resolver.
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", agents)
    monkeypatch.setattr("gaia.tools.serve.base.constants.AGENTS_DIR", agents)
    return ws


async def test_serve_returns_reachable_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _workspace(tmp_path, monkeypatch)
    mgr = StaticServerManager(ServedPorts(), idle_seconds=999)
    try:
        site, url = await mgr.serve(str(ws))
        body = await asyncio.to_thread(
            lambda: urllib.request.urlopen(url + "index.html", timeout=3).read().decode()
        )
        assert body == "<h1>hi</h1>"
        assert url == f"http://127.0.0.1:{site.port}/"
    finally:
        await mgr.close_all()


async def test_serve_registers_and_releases_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path, monkeypatch)
    served = ServedPorts()
    mgr = StaticServerManager(served, idle_seconds=999)
    site, _ = await mgr.serve(str(ws))
    assert site.port in served
    await mgr.close_all()
    assert site.port not in served


async def test_serve_is_idempotent_per_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path, monkeypatch)
    mgr = StaticServerManager(ServedPorts(), idle_seconds=999)
    try:
        a, _ = await mgr.serve(str(ws))
        b, _ = await mgr.serve(str(ws))
        assert a.port == b.port and len(mgr.list()) == 1
    finally:
        await mgr.close_all()


async def test_serve_rejects_path_outside_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    mgr = StaticServerManager(ServedPorts(), idle_seconds=999)
    with pytest.raises(ServeError):
        await mgr.serve("/etc")


async def test_serve_file_serves_parent_points_at_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path, monkeypatch)
    root, entry = _resolve_under_agents(str(ws / "index.html"))
    assert root == ws.resolve() and entry == "index.html"


async def test_serve_stop_and_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _workspace(tmp_path, monkeypatch)
    mgr = StaticServerManager(ServedPorts(), idle_seconds=999)
    site, _ = await mgr.serve(str(ws))
    assert len(mgr.list()) == 1
    stopped = await mgr.stop(str(site.port))
    assert stopped is not None and mgr.list() == []
    assert await mgr.stop("99999") is None  # unknown


async def test_serve_tool_returns_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _workspace(tmp_path, monkeypatch)
    mgr = StaticServerManager(ServedPorts(), idle_seconds=999)
    try:
        out = await make_serve(mgr)(str(ws))
        assert out["status"] == "success" and out["url"].startswith("http://127.0.0.1:")
    finally:
        await mgr.close_all()


async def test_serve_tool_errors_never_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    out = await make_serve(StaticServerManager(ServedPorts()))("/etc")
    assert out["status"] == "error"

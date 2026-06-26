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


def test_serve_resolves_relative_against_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: a relative path must resolve against the caller's workspace (like the fs tools),
    # not the process cwd — otherwise the model burns retries before discovering the absolute path.
    from gaia.tools.fs.base import current_agent

    ws = _workspace(tmp_path, monkeypatch)  # agents/demo/workspace + index.html
    (ws / "site").mkdir()
    token = current_agent.set("demo")
    try:
        d_file, entry = _resolve_under_agents("index.html")  # was "can only serve…" before the fix
        assert entry == "index.html" and d_file.name == "workspace"
        d_dir, entry2 = _resolve_under_agents("site")
        assert entry2 == "" and d_dir.name == "site"
        root, _ = _resolve_under_agents(".")
        assert root.name == "workspace"
    finally:
        current_agent.reset(token)


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


async def test_serve_flags_unviewable_for_remote_user_without_tunnel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A remote (whatsapp) user can't open 127.0.0.1 and tunneling is off — serve must flag the
    # url unviewable so the model screenshots instead of pasting it.
    from gaia.connectors.base import current_chat

    ws = _workspace(tmp_path, monkeypatch)
    mgr = StaticServerManager(ServedPorts(), idle_seconds=999)
    token = current_chat.set(("whatsapp", "972@x"))
    try:
        out = await make_serve(mgr)(str(ws))
        assert out["viewable_by_user"] is False
        assert "screenshot" in out["note"] and "public_url" not in out
    finally:
        current_chat.reset(token)
        await mgr.close_all()


async def test_serve_local_cli_user_gets_no_unviewable_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A local cli user can open 127.0.0.1 directly — no flag, no screenshot nudge.
    ws = _workspace(tmp_path, monkeypatch)  # default current_chat is ("", "") = local
    mgr = StaticServerManager(ServedPorts(), idle_seconds=999)
    try:
        out = await make_serve(mgr)(str(ws))
        assert "viewable_by_user" not in out
    finally:
        await mgr.close_all()

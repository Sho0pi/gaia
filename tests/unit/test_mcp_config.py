"""MCP config helpers: add/list/remove servers, ${VAR} header interpolation, env-ref discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia import mcp
from gaia.config.schema import MCPServerConfig


def _cfg(tmp_path: Path) -> Path:
    p = tmp_path / "gaia.yaml"
    p.write_text("mcp:\n  servers: []\n")
    return p


def test_add_read_remove_roundtrip(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mcp.add_server(cfg, name="time", command="uvx", args=["mcp-server-time"])
    servers = mcp.read_servers(cfg)
    assert [s.name for s in servers] == ["time"]
    assert servers[0].command == "uvx" and servers[0].args == ["mcp-server-time"]
    assert mcp.remove_server(cfg, "time") is True
    assert mcp.read_servers(cfg) == []
    assert mcp.remove_server(cfg, "time") is False  # already gone


def test_add_rejects_duplicate_and_bad_transport(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mcp.add_server(cfg, name="t", command="uvx")
    with pytest.raises(ValueError, match="already exists"):
        mcp.add_server(cfg, name="t", command="uvx")
    with pytest.raises(ValueError, match="command"):
        mcp.add_server(cfg, name="x", transport="stdio")
    with pytest.raises(ValueError, match="url"):
        mcp.add_server(cfg, name="y", transport="http")


def test_header_var_is_interpolated_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TT_TOKEN", "secret123")
    s = MCPServerConfig(
        name="tt",
        transport="http",
        url="https://x",
        headers={"Authorization": "Bearer ${TT_TOKEN}"},
    )
    assert mcp._headers(s) == {"Authorization": "Bearer secret123"}


def test_expand_env_missing_var_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    assert mcp._expand_env("Bearer ${NOPE}") == "Bearer "


def test_env_refs_covers_passthrough_and_header_vars() -> None:
    s = MCPServerConfig(
        name="tt",
        transport="http",
        url="https://x",
        headers={"Authorization": "Bearer ${TT_TOKEN}"},
        env_passthrough=["OTHER"],
    )
    assert mcp.env_refs(s) == ["OTHER", "TT_TOKEN"]


def test_add_remote_with_secret_header_keeps_the_ref_in_config(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    server = mcp.add_server(
        cfg,
        name="ticktick",
        transport="http",
        url="https://mcp.ticktick.com",
        headers={"Authorization": "Bearer ${TICKTICK_TOKEN}"},
    )
    assert server.url == "https://mcp.ticktick.com"
    # the ${VAR} ref (not the secret) is what lands in gaia.yaml
    assert mcp.read_servers(cfg)[0].headers == {"Authorization": "Bearer ${TICKTICK_TOKEN}"}
    assert mcp.env_refs(server) == ["TICKTICK_TOKEN"]

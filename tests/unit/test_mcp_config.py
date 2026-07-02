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


def test_user_secrets_overlay_the_users_own_store_on_the_global_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaia.cli._envfile import set_env_var

    monkeypatch.setenv("SHARED_KEY", "global")
    set_env_var(mcp.user_secret_path("itay"), "TICKTICK_TOKEN", "itays-token")
    secrets = mcp.user_secrets("itay")
    assert secrets["TICKTICK_TOKEN"] == "itays-token"  # from the user's store
    assert secrets["SHARED_KEY"] == "global"  # global env still visible
    # a different user doesn't see itay's token
    assert "TICKTICK_TOKEN" not in mcp.user_secrets("grace")


def test_manager_scopes_servers_and_secrets_per_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaia.cli._envfile import set_env_var
    from gaia.config import ConfigSupplier

    cfg = _cfg(tmp_path)
    mcp.add_server(cfg, name="pub", command="uvx")  # shared (owner="")
    mcp.add_server(cfg, name="tick", command="uvx", env_passthrough=["TT"], owner="itay")  # private
    set_env_var(mcp.user_secret_path("itay"), "TT", "itays-token")

    captured: dict[str, object] = {}

    def _spy(config: object, secrets: object = None) -> list:
        captured["servers"] = [s.name for s in config.servers]  # type: ignore[attr-defined]
        captured["secrets"] = secrets
        return []

    monkeypatch.setattr("gaia.mcp.build_mcp_toolsets", _spy)
    manager = mcp.McpToolsetManager(lambda: ConfigSupplier(cfg).current)

    manager.for_user("itay")
    assert set(captured["servers"]) == {"pub", "tick"}  # shared + his own  # type: ignore[arg-type]
    assert captured["secrets"]["TT"] == "itays-token"  # type: ignore[index]

    manager.for_user("grace")
    assert captured["servers"] == ["pub"]  # NOT itay's private tick
    assert "TT" not in captured["secrets"]  # type: ignore[operator]  # and not his token


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

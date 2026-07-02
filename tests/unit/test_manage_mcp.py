"""The manage_mcp tool: add/list/remove MCP servers in gaia.yaml, admin-gated, hot-reset."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.config import ConfigSupplier
from gaia.config.schema import GaiaConfig
from gaia.tools.manage_mcp import make_manage_mcp
from gaia.users import UserStore

#: Off the dispatch path (user_id None) = trusted caller, so the admin gate is skipped.
_CTX = SimpleNamespace(user_id=None)


class _Reset:
    def __init__(self) -> None:
        self.calls = 0

    def reset(self) -> None:
        self.calls += 1


def _gaia(tmp_path: Path, users: Any = None) -> tuple[Any, Path, _Reset]:
    cfg = tmp_path / "gaia.yaml"
    cfg.write_text("mcp:\n  servers: []\n")
    reset = _Reset()
    gaia = SimpleNamespace(
        users=users or SimpleNamespace(get=lambda _id: None),
        settings=SimpleNamespace(config_path=cfg),
        config=GaiaConfig(),
        container=SimpleNamespace(mcp_toolsets=reset),
    )
    return gaia, cfg, reset


def _servers(cfg: Path) -> list[Any]:
    return list(ConfigSupplier(cfg).current.mcp.servers)


async def test_add_writes_server_and_resets_toolsets(tmp_path: Path) -> None:
    gaia, cfg, reset = _gaia(tmp_path)
    out = await make_manage_mcp(gaia)(
        "add", name="time", command="uvx", args=["mcp-server-time"], tool_context=_CTX
    )
    assert out["status"] == "success" and out["added"] == "time"
    servers = _servers(cfg)
    assert [s.name for s in servers] == ["time"]
    assert servers[0].command == "uvx" and servers[0].args == ["mcp-server-time"]
    assert reset.calls == 1  # next turn rebuilds toolsets with the new server


async def test_add_stdio_requires_a_command(tmp_path: Path) -> None:
    gaia, cfg, _ = _gaia(tmp_path)
    out = await make_manage_mcp(gaia)("add", name="x", command="", tool_context=_CTX)
    assert out["status"] == "error" and "command" in out["error_message"]
    assert _servers(cfg) == []


async def test_add_remote_requires_a_url(tmp_path: Path) -> None:
    gaia, _, _ = _gaia(tmp_path)
    out = await make_manage_mcp(gaia)("add", name="x", transport="http", tool_context=_CTX)
    assert out["status"] == "error" and "url" in out["error_message"]


async def test_add_rejects_duplicate_name(tmp_path: Path) -> None:
    gaia, _, _ = _gaia(tmp_path)
    tool = make_manage_mcp(gaia)
    await tool("add", name="time", command="uvx", tool_context=_CTX)
    out = await tool("add", name="time", command="uvx", tool_context=_CTX)
    assert out["status"] == "error" and "already exists" in out["error_message"]


async def test_env_passthrough_is_surfaced_for_key_guidance(tmp_path: Path) -> None:
    gaia, cfg, _ = _gaia(tmp_path)
    out = await make_manage_mcp(gaia)(
        "add",
        name="ticktick",
        command="uvx",
        args=["ticktick-mcp"],
        env_passthrough=["TICKTICK_TOKEN"],
        tool_context=_CTX,
    )
    assert out["needs_env"] == ["TICKTICK_TOKEN"]
    assert "TICKTICK_TOKEN" in out["message"] and "~/.gaia/.env" in out["message"]
    # the secret var name is recorded in config; the value is never handled here
    assert _servers(cfg)[0].env_passthrough == ["TICKTICK_TOKEN"]


async def test_list_then_remove(tmp_path: Path) -> None:
    gaia, cfg, reset = _gaia(tmp_path)
    tool = make_manage_mcp(gaia)
    await tool("add", name="time", command="uvx", tool_context=_CTX)
    listed = await tool("list", tool_context=_CTX)
    assert [s["name"] for s in listed["servers"]] == ["time"]
    out = await tool("remove", name="time", tool_context=_CTX)
    assert out["status"] == "success" and _servers(cfg) == []
    assert reset.calls == 2  # add + remove each reset


async def test_remove_unknown_is_an_error(tmp_path: Path) -> None:
    gaia, _, reset = _gaia(tmp_path)
    out = await make_manage_mcp(gaia)("remove", name="nope", tool_context=_CTX)
    assert out["status"] == "error" and reset.calls == 0


async def test_non_admin_is_refused(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("whatsapp", "1@s.whatsapp.net", "Bob", role="user")
    gaia, cfg, reset = _gaia(tmp_path, users=store)
    out = await make_manage_mcp(gaia)(
        "add", name="x", command="uvx", tool_context=SimpleNamespace(user_id="bob")
    )
    assert out["status"] == "error" and "admin" in out["error_message"]
    assert reset.calls == 0 and _servers(cfg) == []

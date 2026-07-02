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


class _Manager:
    """Fake McpToolsetManager — counts invalidations (the per-user toolset cache clear)."""

    def __init__(self) -> None:
        self.invalidations = 0

    async def invalidate_all(self) -> None:
        self.invalidations += 1


def _gaia(tmp_path: Path, users: Any = None) -> tuple[Any, Path, _Manager]:
    cfg = tmp_path / "gaia.yaml"
    cfg.write_text("mcp:\n  servers: []\n")
    manager = _Manager()
    gaia = SimpleNamespace(
        users=users or SimpleNamespace(get=lambda _id: None),
        settings=SimpleNamespace(config_path=cfg),
        config=GaiaConfig(),
        container=SimpleNamespace(mcp_toolsets_manager=lambda: manager),
    )
    return gaia, cfg, manager


def _servers(cfg: Path) -> list[Any]:
    return list(ConfigSupplier(cfg).current.mcp.servers)


async def test_add_writes_server_and_resets_toolsets(tmp_path: Path) -> None:
    gaia, cfg, manager = _gaia(tmp_path)
    out = await make_manage_mcp(gaia)(
        "add", name="time", command="uvx", args=["mcp-server-time"], tool_context=_CTX
    )
    assert out["status"] == "success" and out["added"] == "time"
    servers = _servers(cfg)
    assert [s.name for s in servers] == ["time"]
    assert servers[0].command == "uvx" and servers[0].args == ["mcp-server-time"]
    assert manager.invalidations == 1  # next turn rebuilds toolsets with the new server


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
    assert "save_secret" in out["message"]  # steers the user to the secure secret-capture flow
    # the secret var name is recorded in config; the value is never handled here
    assert _servers(cfg)[0].env_passthrough == ["TICKTICK_TOKEN"]


async def test_list_then_remove(tmp_path: Path) -> None:
    gaia, cfg, manager = _gaia(tmp_path)
    tool = make_manage_mcp(gaia)
    await tool("add", name="time", command="uvx", tool_context=_CTX)
    listed = await tool("list", tool_context=_CTX)
    assert [s["name"] for s in listed["servers"]] == ["time"]
    out = await tool("remove", name="time", tool_context=_CTX)
    assert out["status"] == "success" and _servers(cfg) == []
    assert manager.invalidations == 2  # add + remove each reset


async def test_remove_unknown_is_an_error(tmp_path: Path) -> None:
    gaia, _, manager = _gaia(tmp_path)
    out = await make_manage_mcp(gaia)("remove", name="nope", tool_context=_CTX)
    assert out["status"] == "error" and manager.invalidations == 0


async def test_add_is_private_to_caller_shared_is_opt_in(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("whatsapp", "1@s.whatsapp.net", "Itay", role="admin")
    gaia, cfg, _ = _gaia(tmp_path, users=store)
    tool = make_manage_mcp(gaia)
    ctx = SimpleNamespace(user_id="itay")
    await tool("add", name="tick", command="uvx", tool_context=ctx)  # private (default)
    await tool("add", name="time", command="uvx", shared=True, tool_context=ctx)  # shared
    owners = {s.name: s.owner for s in _servers(cfg)}
    assert owners == {"tick": "itay", "time": ""}


async def test_list_shows_only_shared_and_your_own(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("whatsapp", "1@s.whatsapp.net", "Itay", role="admin")
    store.register("whatsapp", "2@s.whatsapp.net", "Grace", role="admin")
    gaia, _, _ = _gaia(tmp_path, users=store)
    tool = make_manage_mcp(gaia)
    await tool("add", name="tick", command="uvx", tool_context=SimpleNamespace(user_id="itay"))
    # Grace can't see Itay's private server
    grace = await tool("list", tool_context=SimpleNamespace(user_id="grace"))
    assert [s["name"] for s in grace["servers"]] == []
    itay = await tool("list", tool_context=SimpleNamespace(user_id="itay"))
    assert [s["name"] for s in itay["servers"]] == ["tick"]


async def test_non_admin_is_refused(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("whatsapp", "1@s.whatsapp.net", "Bob", role="user")
    gaia, cfg, manager = _gaia(tmp_path, users=store)
    out = await make_manage_mcp(gaia)(
        "add", name="x", command="uvx", tool_context=SimpleNamespace(user_id="bob")
    )
    assert out["status"] == "error" and "admin" in out["error_message"]
    assert manager.invalidations == 0 and _servers(cfg) == []

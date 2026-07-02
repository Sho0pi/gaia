"""The /mcp slash command: list / add / remove, sharing the gaia.mcp config helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gaia.commands import default_registry
from gaia.commands.base import CommandContext
from gaia.commands.mcp import MCPCommand


class _Manager:
    def __init__(self) -> None:
        self.invalidations = 0

    async def invalidate_all(self) -> None:
        self.invalidations += 1


def _harness(tmp_path: Path) -> tuple[Path, _Manager]:
    cfg = tmp_path / "gaia.yaml"
    cfg.write_text("mcp:\n  servers: []\n")
    return cfg, _Manager()


def _ctx(cfg: Path, manager: _Manager, args: str, *, user_id: str = "itay") -> CommandContext:
    gaia = SimpleNamespace(
        settings=SimpleNamespace(config_path=cfg),
        container=SimpleNamespace(mcp_toolsets_manager=lambda: manager),
    )
    return CommandContext(
        args=args,
        gaia=gaia,  # type: ignore[arg-type]
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=default_registry(),
        user_id=user_id,
        session_id="s",
        role="admin",
    )


async def test_add_list_remove(tmp_path: Path) -> None:
    cfg, manager = _harness(tmp_path)
    cmd = MCPCommand()

    added = await cmd.run(_ctx(cfg, manager, "add time uvx mcp-server-time"))
    assert "Added" in added and manager.invalidations == 1

    listed = await cmd.run(_ctx(cfg, manager, "list"))
    assert "time" in listed and "stdio" in listed

    empty_args = await cmd.run(_ctx(cfg, manager, ""))  # no args -> list
    assert "time" in empty_args

    removed = await cmd.run(_ctx(cfg, manager, "remove time"))
    assert "Removed" in removed and manager.invalidations == 2


async def test_add_is_private_and_hidden_from_others(tmp_path: Path) -> None:
    cfg, manager = _harness(tmp_path)
    cmd = MCPCommand()
    await cmd.run(_ctx(cfg, manager, "add tick uvx tick-mcp", user_id="itay"))
    from gaia import mcp as mcp_cfg

    assert mcp_cfg.read_servers(cfg)[0].owner == "itay"  # private to the adder
    # another user's /mcp list doesn't show it
    listed = await cmd.run(_ctx(cfg, manager, "list", user_id="grace"))
    assert "tick" not in listed


async def test_add_remote_url(tmp_path: Path) -> None:
    cfg, manager = _harness(tmp_path)
    out = await MCPCommand().run(_ctx(cfg, manager, "add tt https://mcp.ticktick.com"))
    assert "Added" in out
    from gaia import mcp as mcp_cfg

    servers = mcp_cfg.read_servers(cfg)
    assert servers[0].transport == "http" and servers[0].url == "https://mcp.ticktick.com"


async def test_remove_unknown(tmp_path: Path) -> None:
    cfg, manager = _harness(tmp_path)
    out = await MCPCommand().run(_ctx(cfg, manager, "remove nope"))
    assert "No MCP server" in out and manager.invalidations == 0

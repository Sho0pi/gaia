"""The /mcp slash command: list / add / remove, sharing the gaia.mcp config helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gaia.commands import default_registry
from gaia.commands.base import CommandContext
from gaia.commands.mcp import MCPCommand


class _Reset:
    def __init__(self) -> None:
        self.calls = 0

    def reset(self) -> None:
        self.calls += 1


def _harness(tmp_path: Path) -> tuple[Path, _Reset]:
    cfg = tmp_path / "gaia.yaml"
    cfg.write_text("mcp:\n  servers: []\n")
    return cfg, _Reset()


def _ctx(cfg: Path, reset: _Reset, args: str) -> CommandContext:
    gaia = SimpleNamespace(
        settings=SimpleNamespace(config_path=cfg),
        container=SimpleNamespace(mcp_toolsets=reset),
    )
    return CommandContext(
        args=args,
        gaia=gaia,  # type: ignore[arg-type]
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=default_registry(),
        user_id="itay",
        session_id="s",
        role="admin",
    )


async def test_add_list_remove(tmp_path: Path) -> None:
    cfg, reset = _harness(tmp_path)
    cmd = MCPCommand()

    added = await cmd.run(_ctx(cfg, reset, "add time uvx mcp-server-time"))
    assert "Added" in added and reset.calls == 1

    listed = await cmd.run(_ctx(cfg, reset, "list"))
    assert "time" in listed and "stdio" in listed

    empty_args = await cmd.run(_ctx(cfg, reset, ""))  # no args -> list
    assert "time" in empty_args

    removed = await cmd.run(_ctx(cfg, reset, "remove time"))
    assert "Removed" in removed and reset.calls == 2


async def test_add_remote_url(tmp_path: Path) -> None:
    cfg, reset = _harness(tmp_path)
    out = await MCPCommand().run(_ctx(cfg, reset, "add tt https://mcp.ticktick.com"))
    assert "Added" in out
    from gaia import mcp as mcp_cfg

    servers = mcp_cfg.read_servers(cfg)
    assert servers[0].transport == "http" and servers[0].url == "https://mcp.ticktick.com"


async def test_remove_unknown(tmp_path: Path) -> None:
    cfg, reset = _harness(tmp_path)
    out = await MCPCommand().run(_ctx(cfg, reset, "remove nope"))
    assert "No MCP server" in out and reset.calls == 0

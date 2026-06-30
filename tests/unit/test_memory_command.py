"""/memory toggle + embedder-provider switch: admin-gated, writes gaia.yaml, resets the store."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from gaia import constants
from gaia.commands.base import CommandContext
from gaia.commands.memory import MemoryCommand
from gaia.config import GaiaConfig
from gaia.config.schema import MemoryConfig, MemoryProvider


def _ctx(
    tmp_path: Path,
    *,
    args: str = "",
    role: str = "admin",
    enabled: bool = True,
    provider: str = "gemini",
    okey: str | None = None,
    service: Any = None,
) -> CommandContext:
    memory = MemoryConfig(enabled=enabled, embedder=MemoryProvider(provider=provider))
    gaia = SimpleNamespace(
        config=GaiaConfig(memory=memory),
        settings=SimpleNamespace(
            config_path=tmp_path / "gaia.yaml", google_api_key="g-key", openai_api_key=okey
        ),
        users=SimpleNamespace(get=lambda _u: None),  # unresolved → the context role decides admin
        memory_service=service,
    )
    return CommandContext(
        args=args,
        gaia=gaia,  # type: ignore[arg-type]
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=SimpleNamespace(),  # type: ignore[arg-type]
        user_id="u1",
        session_id="s1",
        role=role,
    )


def _yaml(tmp_path: Path) -> dict[str, Any]:
    return yaml.safe_load((tmp_path / "gaia.yaml").read_text())


async def test_off_then_on_writes_enabled(tmp_path: Path) -> None:
    assert "off" in (await MemoryCommand().run(_ctx(tmp_path, args="off"))).lower()
    assert _yaml(tmp_path)["memory"]["enabled"] is False

    assert "on" in (await MemoryCommand().run(_ctx(tmp_path, args="on"))).lower()
    assert _yaml(tmp_path)["memory"]["enabled"] is True


async def test_switch_provider_writes_and_resets_store(tmp_path: Path) -> None:
    # a pre-existing chroma store (in the per-test tmp HOME_DIR) is wiped on switch
    store = constants.HOME_DIR / "memory" / "chroma"
    store.mkdir(parents=True)
    (store / "vectors.bin").write_text("x")

    out = await MemoryCommand().run(_ctx(tmp_path, args="openai", okey="sk-x"))

    assert _yaml(tmp_path)["memory"]["embedder"]["provider"] == "openai"
    assert _yaml(tmp_path)["memory"]["enabled"] is True
    assert not store.exists()  # store reset (vectors incompatible with the new embedder)
    assert "restart" in out.lower()


async def test_switch_to_openai_without_key_warns(tmp_path: Path) -> None:
    out = await MemoryCommand().run(_ctx(tmp_path, args="openai", okey=None))
    assert "OPENAI_API_KEY" in out


async def test_non_admin_cannot_change(tmp_path: Path) -> None:
    for arg in ("off", "openai"):
        out = await MemoryCommand().run(_ctx(tmp_path, args=arg, role="user"))
        assert "admin" in out.lower()
    assert not (tmp_path / "gaia.yaml").exists()  # nothing written


async def test_status_line_when_on(tmp_path: Path) -> None:
    out = await MemoryCommand().run(_ctx(tmp_path, enabled=True, provider="gemini"))
    assert "on" in out.lower() and "gemini" in out

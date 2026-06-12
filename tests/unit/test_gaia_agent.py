"""Gaia.build_root_agent attaches every registered tool to the root agent by default."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.config import Settings
from gaia.core import Gaia


def _gaia(tmp_path: Path) -> Gaia:
    # web_search is installed only when an engine is configured.
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text("tools:\n  web_search:\n    engine: duckduckgo\n")
    settings = Settings(agent_registry_dir=tmp_path / "registry", config_path=config_path)
    return Gaia(settings)


def _capture_root_kwargs(gaia: Gaia, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    import google.adk.agents as adk

    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(adk, "LlmAgent", _Recorder)
    gaia.build_root_agent()
    return captured


def test_root_agent_attaches_all_registered_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia = _gaia(tmp_path)

    kwargs = _capture_root_kwargs(gaia, monkeypatch)

    # The root agent gets every registered tool plus the root-only delegate_to_soul tool.
    tools = kwargs["tools"]
    assert gaia.tools.all() == tools[: len(gaia.tools.all())]  # registry tools come first
    # web_fetch + the fs_* bundle are on by default, web_search via the configured engine
    # (fs_glob/fs_grep depend on fd/rg), and delegate_to_soul is appended for Gaia alone.
    names = {getattr(t, "__name__", t) for t in tools}  # type: ignore[union-attr]
    expected = {"web_fetch", "web_search", "fs_read", "fs_write", "fs_edit", "delegate_to_soul"}
    assert expected <= names


async def test_close_runs_tool_cleanup_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Gaia.close must release the tool managers (shell/browser) AND the MCP toolsets on
    # the running loop — even if one of them raises — and be idempotent.
    gaia = _gaia(tmp_path)
    calls: list[str] = []

    async def fake_aclose() -> None:
        calls.append("tools")

    monkeypatch.setattr(gaia.tools, "aclose", fake_aclose)

    class _Toolset:
        async def close(self) -> None:
            calls.append("mcp")

    gaia._mcp = [_Toolset()]  # type: ignore[list-item]

    await gaia.close()
    await gaia.close()  # idempotent: second call does nothing

    assert calls == ["tools", "mcp"]


async def test_async_context_manager_closes_even_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``async with gaia`` is the launchers' lifetime scope: close must run on exit,
    # exception paths included (that's the whole point over a manual close call).
    gaia = _gaia(tmp_path)
    calls: list[str] = []

    async def fake_aclose() -> None:
        calls.append("tools")

    monkeypatch.setattr(gaia.tools, "aclose", fake_aclose)

    with pytest.raises(RuntimeError):
        async with gaia as entered:
            assert entered is gaia
            raise RuntimeError("boom")

    assert calls == ["tools"]

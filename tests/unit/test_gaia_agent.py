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

    # Registry tools are attached through the dynamic AclToolset (resolved per turn against
    # the caller's capabilities), not as a flat list; the root-only tools follow it.
    from gaia.core.acl_toolset import AclToolset

    tools = kwargs["tools"]
    assert isinstance(tools[0], AclToolset)
    # delegate_to_soul / message_user / manage_permission are appended for Gaia alone.
    # delegate_to_soul is a LongRunningFunctionTool (a BaseTool, name on ``.name``), the others
    # are bare callables (``__name__``) — read whichever each exposes.
    names = {getattr(t, "__name__", None) or getattr(t, "name", t) for t in tools}  # type: ignore[union-attr]
    expected = {"delegate_to_soul", "message_user", "manage_permission"}
    assert expected <= names


def test_profile_is_baked_into_the_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import google.adk.agents as adk

    gaia = _gaia(tmp_path)
    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(adk, "LlmAgent", _Recorder)
    gaia.build_root_agent(profile="- Name: Itay\n- follows football (Arsenal)")

    instruction = captured["instruction"]
    assert isinstance(instruction, str)
    # The closing tag only comes from the injected block (the static guidance mentions the
    # opening tag), so it's the reliable marker that the profile was actually baked in.
    assert "</USER_PROFILE>" in instruction
    assert "Name: Itay" in instruction and "Arsenal" in instruction


def test_no_profile_block_without_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia = _gaia(tmp_path)

    captured = _capture_root_kwargs(gaia, monkeypatch)  # build_root_agent() — no profile

    assert "</USER_PROFILE>" not in captured["instruction"]  # type: ignore[operator]


def test_root_agent_attaches_skill_toolset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # On-demand skills: when skills_dir holds a skill, the root agent gets a SkillToolset.
    skills = tmp_path / "skills"
    (skills / "web-research").mkdir(parents=True)
    (skills / "web-research" / "SKILL.md").write_text(
        "---\nname: web-research\ndescription: search\n---\n\nbody\n"
    )
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text(f"skills_dir: {skills}\n")
    settings = Settings(agent_registry_dir=tmp_path / "registry", config_path=config_path)
    gaia = Gaia(settings)

    from google.adk.tools.skill_toolset import SkillToolset

    kwargs = _capture_root_kwargs(gaia, monkeypatch)
    assert any(isinstance(t, SkillToolset) for t in kwargs["tools"])  # type: ignore[union-attr]


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

    async def fake_mcp_close() -> None:
        calls.append("mcp")

    # Container-resource cleanup is mediated by LifecycleManager — register the
    # fake closer the same way a real builder would have, then trigger close.
    gaia.container.lifecycle().add(fake_mcp_close)

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

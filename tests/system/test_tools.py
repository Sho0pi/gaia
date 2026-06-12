"""System test: a subagent that declares a tool builds with that tool attached.

Skipped unless a Gemini key is configured, so CI stays green without secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gaia.agents import AgentSpec
from gaia.config import Settings
from gaia.core import Gaia

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]


# backend: native keeps souls free of the default playwright-mcp toolset, so these
# build tests assert exactly the declared tool regardless of whether bun is installed.
_NATIVE_BROWSER = "browser:\n  backend: native\n"


def test_subagent_with_web_search_builds(tmp_path: Path) -> None:
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text(_NATIVE_BROWSER + "tools:\n  web_search:\n    engine: duckduckgo\n")
    settings = Settings(agent_registry_dir=tmp_path, config_path=config_path)
    gaia = Gaia(settings)
    spec = AgentSpec(
        name="Researcher",
        description="Looks things up online.",
        instruction="Research the user's question using web_search.",
        model=settings.model,
        tools=["web_search"],
    )

    agent = gaia.ensure_agent(spec)

    assert agent.name == "researcher"
    assert len(agent.tools) == 1


def test_subagent_with_web_fetch_builds(tmp_path: Path) -> None:
    # web_fetch needs no config; it is on by default.
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text(_NATIVE_BROWSER)
    settings = Settings(agent_registry_dir=tmp_path, config_path=config_path)
    gaia = Gaia(settings)
    spec = AgentSpec(
        name="Reader",
        description="Reads web pages.",
        instruction="Fetch and summarise pages using web_fetch.",
        model=settings.model,
        tools=["web_fetch"],
    )

    agent = gaia.ensure_agent(spec)

    assert agent.name == "reader"
    assert len(agent.tools) == 1


def test_subagent_with_fs_read_builds(tmp_path: Path) -> None:
    # fs_read needs no config; it is on by default.
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text(_NATIVE_BROWSER)
    settings = Settings(agent_registry_dir=tmp_path, config_path=config_path)
    gaia = Gaia(settings)
    spec = AgentSpec(
        name="Filer",
        description="Reads files.",
        instruction="Read files from the workspace using fs_read.",
        model=settings.model,
        tools=["fs_read"],
    )

    agent = gaia.ensure_agent(spec)

    assert agent.name == "filer"
    assert len(agent.tools) == 1

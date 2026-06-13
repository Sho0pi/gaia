"""System test: Gaia's root agent carries the memory tools when memory is on.

Skipped unless a Gemini key is configured, so CI stays green without secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gaia.config import Settings
from gaia.core import Gaia

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]


def _tool_names(gaia: Gaia) -> set[str]:
    return {getattr(t, "name", getattr(t, "__name__", "")) for t in gaia.tools.all()}


def test_root_agent_has_memory_tools_by_default(tmp_path: Path) -> None:
    # Memory is on by default; load_memory (read) + remember (write) are registered.
    settings = Settings(agent_registry_dir=tmp_path, config_path=tmp_path / "gaia.yaml")
    gaia = Gaia(settings)

    root = gaia.build_root_agent()

    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in root.tools}
    assert {"load_memory", "remember"} <= names


def test_memory_tools_dropped_when_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text("memory:\n  enabled: false\n")
    settings = Settings(agent_registry_dir=tmp_path, config_path=config_path)
    gaia = Gaia(settings)

    assert {"load_memory", "remember"}.isdisjoint(_tool_names(gaia))
    assert gaia.memory_service is None  # no service when memory is off

"""System test: ``gaia soul create --ai`` forges a real, valid soul via the smith.

Skipped unless a Gemini key is configured, so CI stays green without secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.agents import SoulRegistry
from gaia.cli import app as cli_app
from gaia.config import Settings

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]

runner = CliRunner()


def test_create_ai_forges_valid_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = tmp_path / "agent_registry"
    settings = Settings(agent_registry_dir=reg, config_path=tmp_path / "gaia.yaml")
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)

    result = runner.invoke(
        cli_app, ["soul", "create", "Mailer", "--ai", "summarize my email", "--yes"]
    )

    assert result.exit_code == 0, result.output
    spec = SoulRegistry(reg).get("mailer")  # NAME overrides the smith's chosen name
    assert spec is not None
    assert spec.name == "Mailer"
    assert spec.instruction  # the smith authored a non-empty system prompt

"""``gaia config`` group: path/get/set against a tmp gaia.yaml via CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp gaia.yaml wired through the light ``get_settings`` the commands call."""
    path = tmp_path / "gaia.yaml"
    path.write_text("llm:\n  model: gemini-2.0-flash\nmissions:\n  max_tasks: 20\n")
    settings = Settings(config_path=path)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    return path


def test_path(config_path: Path) -> None:
    result = runner.invoke(cli_app, ["--json", "config", "path"])
    assert result.exit_code == 0
    assert json.loads(result.output)["path"] == str(config_path)


def test_get_effective_value(config_path: Path) -> None:
    result = runner.invoke(cli_app, ["--json", "config", "get", "llm.model"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"llm.model": "gemini-2.0-flash"}


def test_get_unknown_key_exits_1(config_path: Path) -> None:
    assert runner.invoke(cli_app, ["config", "get", "no.such.key"]).exit_code == 1


def test_set_round_trips(config_path: Path) -> None:
    assert runner.invoke(cli_app, ["config", "set", "missions.max_tasks", "30"]).exit_code == 0
    result = runner.invoke(cli_app, ["--json", "config", "get", "missions.max_tasks"])
    assert json.loads(result.output) == {"missions.max_tasks": 30}  # schema coerced "30" → int

"""``gaia memory`` group: the memory-off path (deterministic, no key) via CliRunner.

The real list/forget needs mem0 + a model key (a system concern); the offline-checkable behavior
is that the command builds a Gaia, finds memory disabled, and exits 1 with a clear message.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture
def memory_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "gaia.yaml"
    path.write_text("memory:\n  enabled: false\n")
    settings = Settings(config_path=path, agent_registry_dir=tmp_path / "reg")
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)


def test_list_when_memory_off_exits_1(memory_off: None) -> None:
    result = runner.invoke(cli_app, ["memory", "list", "itay"])
    assert result.exit_code == 1
    assert "off" in result.output.lower()


def test_forget_when_memory_off_exits_1(memory_off: None) -> None:
    assert runner.invoke(cli_app, ["memory", "forget", "itay", "--yes"]).exit_code == 1

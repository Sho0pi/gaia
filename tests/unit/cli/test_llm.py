"""``godpy llm`` group: auth delegation and group help."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from godpy.cli import app

runner = CliRunner()


def test_auth_openai_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        "godpy.app.run_auth",
        lambda provider, *, env_file=None: called.update(provider=provider, env_file=env_file),
    )
    result = runner.invoke(app, ["llm", "auth", "openai"])
    assert result.exit_code == 0
    assert called == {"provider": "openai", "env_file": None}


def test_auth_forwards_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        "godpy.app.run_auth",
        lambda provider, *, env_file=None: called.update(provider=provider, env_file=env_file),
    )
    env = tmp_path / ".env"
    result = runner.invoke(app, ["--env-file", str(env), "llm", "auth", "openai"])
    assert result.exit_code == 0
    assert called == {"provider": "openai", "env_file": env}


def test_llm_help_shows_auth() -> None:
    result = runner.invoke(app, ["llm", "--help"])
    assert result.exit_code == 0
    assert "auth" in result.output

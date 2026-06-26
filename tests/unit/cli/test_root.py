"""Root CLI: help tree, version, global flags, and command delegation.

The app commands import ``gaia.app`` lazily inside their bodies, so monkeypatching
the ``gaia.app`` attributes before invoking intercepts the call without running Gaia.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gaia import __version__
from gaia.cli import app

runner = CliRunner()


def test_help_lists_command_tree() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    commands = ("chat", "dev", "model", "version", "serve", "start", "stop", "restart", "status")
    for command in commands:
        assert command in result.output


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"gaia {__version__}" in result.output


def test_version_command_human() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"gaia {__version__}" in result.output
    assert "python" in result.output


def test_version_command_json() -> None:
    result = runner.invoke(app, ["--json", "version"])
    assert result.exit_code == 0
    info = json.loads(result.output)
    assert info["version"] == __version__
    assert set(info) == {"name", "version", "python", "location"}


def test_bare_invocation_opens_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        "gaia.app.run_cli",
        lambda settings=None, *, env_file=None: called.update(env_file=env_file),
    )
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert called == {"env_file": None}


def test_chat_forwards_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        "gaia.app.run_cli",
        lambda settings=None, *, env_file=None: called.update(env_file=env_file),
    )
    env = tmp_path / ".env"
    result = runner.invoke(app, ["--env-file", str(env), "chat"])
    assert result.exit_code == 0
    assert called == {"env_file": env}


def test_dev_forwards_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        "gaia.app.run_dev",
        lambda settings=None, *, env_file=None, host="127.0.0.1", port=8000: called.update(
            env_file=env_file, host=host, port=port
        ),
    )
    result = runner.invoke(app, ["dev", "--host", "0.0.0.0", "--port", "9001"])
    assert result.exit_code == 0
    assert called == {"env_file": None, "host": "0.0.0.0", "port": 9001}


def test_no_color_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "0")  # pre-set so monkeypatch restores it after
    result = runner.invoke(app, ["--no-color", "version"])
    assert result.exit_code == 0
    assert os.environ["NO_COLOR"] == "1"


def test_unknown_command_is_usage_error() -> None:
    result = runner.invoke(app, ["bogus"])
    assert result.exit_code == 2

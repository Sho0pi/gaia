"""Unit tests for the connector launch policy (pure, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

import godpy.app as app
from godpy.app import plan_launch
from godpy.config import GodConfig, Settings


def _config(**enabled: bool) -> GodConfig:
    return GodConfig.model_validate(
        {"connectors": {name: {"enabled": on} for name, on in enabled.items()}}
    )


def test_nothing_enabled_launches_nothing() -> None:
    assert plan_launch(GodConfig()) == []


def test_background_connectors_selected() -> None:
    config = _config(whatsapp=True, telegram=True)

    assert plan_launch(config) == ["whatsapp", "telegram"]


def test_cli_only_is_allowed() -> None:
    assert plan_launch(_config(cli=True)) == ["cli"]


def test_cli_with_background_is_rejected() -> None:
    config = _config(cli=True, whatsapp=True)

    with pytest.raises(ValueError, match="foreground-exclusive"):
        plan_launch(config)


class _FakeConnector:
    """Stands in for CLIConnector: accepts the handler, run() returns immediately."""

    def __init__(self, handler: object) -> None:
        self.handler = handler

    def run(self) -> None:
        return None


def _capture_setup_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_setup(settings: object, cfg: object, **kwargs: object) -> Path:
        captured.update(kwargs)
        return tmp_path

    monkeypatch.setattr(app, "setup_logging", fake_setup)
    monkeypatch.setattr(app, "CLIConnector", _FakeConnector)
    return captured


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        agent_registry_dir=tmp_path / "reg",
        config_path=tmp_path / "god.yaml",
        log_dir=tmp_path / "logs",
    )


def test_run_cli_disables_console_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The TUI owns the terminal — console handlers would draw over the chat UI.
    captured = _capture_setup_logging(monkeypatch, tmp_path)

    app.run_cli(_settings(tmp_path))

    assert captured.get("console") is False


def test_run_with_cli_enabled_disables_console_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_setup_logging(monkeypatch, tmp_path)
    settings = _settings(tmp_path)
    settings.config_path.write_text("connectors:\n  cli:\n    enabled: true\n")

    app.run(settings)

    assert captured.get("console") is False


def test_run_without_cli_keeps_console_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_setup_logging(monkeypatch, tmp_path)

    app.run(_settings(tmp_path))  # nothing enabled -> background path, console stays on

    assert captured.get("console") is True

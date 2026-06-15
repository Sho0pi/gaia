"""Unit tests for the connector launch policy (pure, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

import gaia.app as app
from gaia.app import plan_launch
from gaia.config import GaiaConfig, Settings


def _config(**enabled: bool) -> GaiaConfig:
    return GaiaConfig.model_validate(
        {"connectors": {name: {"enabled": on} for name, on in enabled.items()}}
    )


def test_nothing_enabled_launches_nothing() -> None:
    assert plan_launch(GaiaConfig()) == []


def test_background_connectors_selected() -> None:
    config = _config(whatsapp=True, telegram=True)

    assert plan_launch(config) == ["whatsapp", "telegram"]


def test_cli_only_is_allowed() -> None:
    assert plan_launch(_config(cli=True)) == ["cli"]


def test_cli_with_background_is_rejected() -> None:
    config = _config(cli=True, whatsapp=True)

    with pytest.raises(ValueError, match="foreground-exclusive"):
        plan_launch(config)


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        ({}, []),
        ({"cli": True}, []),  # cli is foreground-only: silently excluded in daemon mode
        ({"cli": True, "telegram": True}, ["telegram"]),  # no ValueError in daemon mode
        ({"whatsapp": True, "telegram": True}, ["whatsapp", "telegram"]),
    ],
)
def test_plan_launch_daemon_mode(enabled: dict[str, bool], expected: list[str]) -> None:
    assert plan_launch(_config(**enabled), daemon=True) == expected


class _FakeConnector:
    """Stands in for CLIConnector: accepts the dispatch, run_async() returns at once."""

    NAME = "cli"

    def __init__(self, dispatch: object) -> None:
        self.dispatch = dispatch

    async def run_async(self) -> None:
        return None


def _capture_run_tui(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    seen: list[Path] = []

    def fake_run_tui(socket_path: Path) -> None:
        seen.append(socket_path)

    monkeypatch.setattr(app, "_run_tui", fake_run_tui)
    return seen


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
        config_path=tmp_path / "gaia.yaml",
        log_dir=tmp_path / "logs",
    )


def test_run_cli_attaches_to_daemon_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_run_tui(monkeypatch)

    app.run_cli(_settings(tmp_path))

    assert seen == [app.constants.SOCKET_FILE]


def test_run_with_cli_enabled_disables_console_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_setup_logging(monkeypatch, tmp_path)
    _capture_run_tui(monkeypatch)
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


def test_run_cli_does_not_build_gaia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run_tui(monkeypatch)

    def fail_gaia(_settings: Settings) -> object:
        raise AssertionError("chat client should attach to daemon, not build Gaia")

    monkeypatch.setattr(app, "Gaia", fail_gaia)

    app.run_cli(_settings(tmp_path))


def test_run_tui_missing_daemon_exits_3(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as exc:
        app._run_tui(tmp_path / "missing.sock")

    assert exc.value.code == 3
    assert "gaia start" in capsys.readouterr().out

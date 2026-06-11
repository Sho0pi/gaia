"""``run_daemon`` lifecycle: pidfile write/remove around the serve loop, plan gating."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import godpy.app as app
from godpy.cli import _pidfile
from godpy.config import GodConfig, Settings


@pytest.fixture(autouse=True)
def pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "godpy.pid"
    monkeypatch.setattr(_pidfile, "PID_FILE", path)
    return path


@pytest.fixture
def quiet_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the heavy bits of run_daemon; record what _serve saw. Returns the recorder."""
    seen: dict[str, Any] = {}

    def fake_god(settings: Settings) -> SimpleNamespace:
        # Parse the real tmp god.yaml so each test's connector setup is honored.
        raw = yaml.safe_load(settings.config_path.read_text()) or {}
        return SimpleNamespace(config=GodConfig.model_validate(raw), close=lambda: None)

    async def fake_serve(settings: Settings, god: Any, selected: list[str], *, hold: bool) -> None:
        seen["selected"] = selected
        seen["hold"] = hold
        seen["pidfile_during_serve"] = _pidfile.read()

    monkeypatch.setattr(app, "God", fake_god)
    monkeypatch.setattr(app, "write_default_config", lambda path: None)
    monkeypatch.setattr(app, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(app, "_serve", fake_serve)
    return seen


def _settings(tmp_path: Path, yaml: str = "") -> Settings:
    config_path = tmp_path / "god.yaml"
    config_path.write_text(yaml)
    return Settings(config_path=config_path, log_dir=tmp_path / "logs")


def test_no_connectors_and_no_hold_exits_1(
    tmp_path: Path, quiet_app: dict[str, Any], pid_file: Path
) -> None:
    code = app.run_daemon(_settings(tmp_path))

    assert code == 1
    assert not pid_file.exists()  # never written: startup not committed
    assert "selected" not in quiet_app  # _serve never ran


def test_hold_runs_with_zero_connectors(
    tmp_path: Path, quiet_app: dict[str, Any], pid_file: Path
) -> None:
    code = app.run_daemon(_settings(tmp_path), hold=True)

    assert code == 0
    assert quiet_app["selected"] == []
    assert quiet_app["hold"] is True


def test_pidfile_written_before_serve_and_removed_after(
    tmp_path: Path, quiet_app: dict[str, Any], pid_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    settings = _settings(tmp_path, "connectors:\n  telegram:\n    enabled: true\n")

    code = app.run_daemon(settings)

    assert code == 0
    assert quiet_app["selected"] == ["telegram"]
    assert quiet_app["pidfile_during_serve"] == os.getpid()  # written before _serve ran
    assert not pid_file.exists()  # removed after


def test_pidfile_removed_when_serve_raises(
    tmp_path: Path, pid_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram_on = GodConfig.model_validate({"connectors": {"telegram": {"enabled": True}}})
    monkeypatch.setattr(
        app, "God", lambda settings: SimpleNamespace(config=telegram_on, close=lambda: None)
    )
    monkeypatch.setattr(app, "write_default_config", lambda path: None)
    monkeypatch.setattr(app, "setup_logging", lambda *a, **k: None)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("connector exploded")

    monkeypatch.setattr(app, "_serve", boom)
    settings = _settings(tmp_path, "connectors:\n  telegram:\n    enabled: true\n")

    with pytest.raises(RuntimeError, match="connector exploded"):
        app.run_daemon(settings)

    assert not pid_file.exists()  # finally cleaned up

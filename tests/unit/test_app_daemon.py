"""``run_daemon`` lifecycle: pidfile write/remove around the serve loop, plan gating."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import gaia.app as app
from gaia.cli._pidfile import PidFile
from gaia.config import GaiaConfig, Settings


@pytest.fixture(autouse=True)
def pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gaia.pid"
    monkeypatch.setattr("gaia.constants.PID_FILE", path)  # PidFile() default
    return path


@pytest.fixture
def quiet_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the heavy bits of run_daemon; record what _serve saw. Returns the recorder."""
    seen: dict[str, Any] = {}

    def fake_gaia(settings: Settings) -> SimpleNamespace:
        # Parse the real tmp gaia.yaml so each test's connector setup is honored.
        raw = yaml.safe_load(settings.config_path.read_text()) or {}
        return SimpleNamespace(config=GaiaConfig.model_validate(raw), close=lambda: None)

    async def fake_serve(settings: Settings, gaia: Any, selected: list[str], *, hold: bool) -> None:
        seen["selected"] = selected
        seen["hold"] = hold
        seen["pidfile_during_serve"] = PidFile().read()

    monkeypatch.setattr(app, "Gaia", fake_gaia)
    monkeypatch.setattr(app, "write_default_config", lambda path: None)
    monkeypatch.setattr(app, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(app, "_serve", fake_serve)
    return seen


def _settings(tmp_path: Path, yaml: str = "") -> Settings:
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text(yaml)
    return Settings(config_path=config_path, log_dir=tmp_path / "logs")


def test_no_background_connectors_still_runs_socket_gateway(
    tmp_path: Path, quiet_app: dict[str, Any], pid_file: Path
) -> None:
    code = app.run_daemon(_settings(tmp_path))

    assert code == 0
    assert quiet_app["selected"] == []
    assert not pid_file.exists()  # removed after serve returns


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
    telegram_on = GaiaConfig.model_validate({"connectors": {"telegram": {"enabled": True}}})
    monkeypatch.setattr(
        app, "Gaia", lambda settings: SimpleNamespace(config=telegram_on, close=lambda: None)
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


def test_run_auth_does_not_build_gaia(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Logging in must not construct a whole Gaia (tool registry, souls, container) just to
    # read the logging config — it reads the config supplier directly.
    monkeypatch.setattr(
        app, "get_settings", lambda _e=None: Settings(config_path=tmp_path / "g.yaml")
    )
    monkeypatch.setattr(app, "setup_logging", lambda *a, **k: None)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("run_auth must not build Gaia")

    monkeypatch.setattr(app, "Gaia", _boom)
    saved: dict[str, Any] = {}

    class _Creds:
        account_id = "acct-1"

        def save(self) -> None:
            saved["ok"] = True

    async def _fake_login() -> Any:
        return _Creds()

    import gaia.providers.openai as openai_pkg

    monkeypatch.setattr(openai_pkg, "login", _fake_login)

    app.run_auth("openai")  # must not raise (Gaia._boom never called)

    assert saved.get("ok") is True

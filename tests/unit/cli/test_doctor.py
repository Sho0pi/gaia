"""``gaia doctor``: each check in isolation over a crafted context, plus end-to-end.

Offline by design — no network, no real keys (dummy secrets only). Checks that read
``constants`` paths directly (home / registry / pidfile) get those patched to tmp.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.cli import doctor
from gaia.cli.doctor import DoctorContext
from gaia.config import GaiaConfig, Settings

runner = CliRunner()


def _ctx(
    *, settings: Settings | None = None, config: GaiaConfig | None = None, error: str | None = None
) -> DoctorContext:
    return DoctorContext(
        settings=settings or Settings(google_api_key=None),  # type: ignore[call-arg]
        config=config if config is not None or error is not None else GaiaConfig(),
        config_error=error,
    )


# --- checks in isolation -----------------------------------------------------------


def test_secrets_fail_missing_gemini_key() -> None:
    result = doctor._check_secrets(_ctx(settings=Settings(google_api_key=None)))  # type: ignore[call-arg]

    assert result.status == "fail"
    assert "GEMINI_API_KEY" in result.message
    assert result.hint


def test_secrets_ok_with_gemini_key() -> None:
    result = doctor._check_secrets(_ctx(settings=Settings(google_api_key="dummy")))  # type: ignore[call-arg]

    assert result.status == "ok"


def test_config_fail_on_parse_error() -> None:
    result = doctor._check_config(_ctx(error="while parsing a block mapping"))

    assert result.status == "fail"
    assert "invalid" in result.message
    assert result.hint


def test_connector_combo_fail_on_invalid_pair() -> None:
    cfg = GaiaConfig.model_validate(
        {"connectors": {"cli": {"enabled": True}, "telegram": {"enabled": True}}}
    )
    result = doctor._check_connector_combo(_ctx(config=cfg))

    assert result.status == "fail"
    assert "foreground-exclusive" in result.message


def test_souls_fail_on_unparseable_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "agent_registry"
    registry.mkdir()
    (registry / "broken.json").write_text("{ not valid")
    monkeypatch.setattr("gaia.constants.AGENT_REGISTRY_DIR", registry)

    result = doctor._check_souls(_ctx())

    assert result.status == "fail"
    assert "broken.json" in result.message


def test_pidfile_warns_on_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = tmp_path / "gaia.pid"
    pid.write_text("999999\n")  # dead pid
    monkeypatch.setattr("gaia.constants.PID_FILE", pid)

    result = doctor._check_pidfile(_ctx())

    assert result.status == "warn"
    assert "stale" in result.message


# --- end to end --------------------------------------------------------------------


@pytest.fixture
def healthy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp home where every check passes (dummy gemini key, writable dirs)."""
    home = tmp_path / ".gaia"
    home.mkdir()
    (home / "logs").mkdir()
    monkeypatch.setattr("gaia.constants.HOME_DIR", home)
    monkeypatch.setattr("gaia.constants.CONFIG_PATH", home / "gaia.yaml")
    monkeypatch.setattr("gaia.constants.AGENT_REGISTRY_DIR", home / "agent_registry")
    monkeypatch.setattr("gaia.constants.PID_FILE", home / "gaia.pid")
    settings = Settings(google_api_key="dummy", log_dir=home / "logs")  # type: ignore[call-arg]
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    return home


def test_doctor_all_ok_exits_0(healthy_home: Path) -> None:
    result = runner.invoke(cli_app, ["doctor"])

    assert result.exit_code == 0
    assert "FAIL" not in result.output


def test_doctor_any_fail_exits_4(healthy_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Drop the only secret so the secrets check FAILs.
    settings = Settings(google_api_key=None, log_dir=healthy_home / "logs")  # type: ignore[call-arg]
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)

    result = runner.invoke(cli_app, ["doctor"])

    assert result.exit_code == doctor.EXIT_DOCTOR
    assert "FAIL" in result.output


def test_doctor_json_carries_per_check_status(healthy_home: Path) -> None:
    result = runner.invoke(cli_app, ["--json", "doctor"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert {c["name"] for c in data["checks"]} == {
        "home",
        "config",
        "secrets",
        "dependencies",
        "connectors",
        "souls",
        "pidfile",
        "log_dir",
    }
    assert all(c["status"] in {"ok", "warn", "fail"} for c in data["checks"])

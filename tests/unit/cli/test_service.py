"""`gaia service` — generated plist/unit text + install/uninstall argv (mocked)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from gaia import constants
from gaia.cli import app, service

runner = CliRunner()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda argv, **_k: (seen.append(argv), SimpleNamespace(returncode=0))[1],
    )
    return seen


def _macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service.os, "getuid", lambda: 501)
    # never touch the real ~/Library/LaunchAgents — point at the isolated tmp home
    monkeypatch.setattr(
        service, "_plist_path", lambda: constants.HOME_DIR / f"{service.LABEL}.plist"
    )


def _linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    monkeypatch.setattr(service, "_unit_path", lambda: constants.HOME_DIR / service.UNIT)


def test_plist_text_has_serve_and_keepalive() -> None:
    text = service._plist_text()
    assert "<string>-m</string><string>gaia.cli</string><string>serve</string>" in text
    assert "<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>" in text
    assert "<key>RunAtLoad</key><true/>" in text


def test_unit_text_restarts_on_failure() -> None:
    text = service._unit_text()
    assert "ExecStart=" in text and " -m gaia.cli serve" in text
    assert "Restart=on-failure" in text


def test_install_macos_writes_plist_and_bootstraps(
    calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _macos(monkeypatch)
    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0, result.output
    assert service._plist_path().exists()  # plist written to the tmp home
    assert any(c[:2] == ["launchctl", "bootstrap"] for c in calls)


def test_install_linux_enables_unit(
    calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _linux(monkeypatch)
    result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 0, result.output
    assert service._unit_path().exists()
    assert ["systemctl", "--user", "enable", "--now", service.UNIT] in calls


def test_uninstall_macos_removes_plist(
    calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _macos(monkeypatch)
    p = service._plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("<plist/>")
    result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0, result.output
    assert not p.exists()
    assert any(c[:2] == ["launchctl", "bootout"] for c in calls)

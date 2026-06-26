"""`gaia report` — bundles a crash + env, files via gh or a prefilled URL (mocked)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from gaia import constants
from gaia.cli import app, report

runner = CliRunner()


def _seed_crash() -> None:
    constants.CRASHES_DIR.mkdir(parents=True, exist_ok=True)
    (constants.CRASHES_DIR / "20260101T000000.json").write_text(
        json.dumps(
            {
                "time": "2026-01-01",
                "gaia_version": "0.1.0a1",
                "error": "ValueError: boom",
                "traceback": "Traceback…\nValueError: boom",
            }
        )
    )


def test_report_files_via_gh_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_crash()
    monkeypatch.setattr(report.shutil, "which", lambda _name: "/usr/bin/gh")
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **_k: Any) -> Any:
        seen["argv"] = argv
        return SimpleNamespace(
            returncode=0, stdout="https://github.com/Sho0pi/gaia/issues/9", stderr=""
        )

    monkeypatch.setattr(report.subprocess, "run", fake_run)
    result = runner.invoke(app, ["report"], input="y\n")
    assert result.exit_code == 0, result.output
    argv = seen["argv"]
    assert argv[:4] == ["gh", "issue", "create", "--repo"] and "--label" in argv
    assert "crash: ValueError: boom" in argv  # title carries the crash signature
    assert "filed:" in result.output


def test_report_opens_url_without_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_crash()
    monkeypatch.setattr(report.shutil, "which", lambda _name: None)  # no gh
    opened: list[str] = []
    monkeypatch.setattr(report.webbrowser, "open", lambda url: opened.append(url))
    result = runner.invoke(app, ["report", "--no-open"], input="y\n")  # print URL, don't open
    assert result.exit_code == 0, result.output
    assert "github.com/Sho0pi/gaia/issues/new" in result.output and "labels=crash" in result.output


def test_report_declined_files_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_crash()
    monkeypatch.setattr(report.shutil, "which", lambda _name: "/usr/bin/gh")
    called = {"run": False}
    monkeypatch.setattr(report.subprocess, "run", lambda *a, **k: called.__setitem__("run", True))
    result = runner.invoke(app, ["report"], input="n\n")  # decline
    assert result.exit_code == 0 and "not filed" in result.output and called["run"] is False

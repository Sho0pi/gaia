"""`gaia update` / `gaia uninstall` — subprocess argv + data-safety (mocked, no real installs)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from gaia import constants
from gaia.cli import app, lifecycle

runner = CliRunner()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every subprocess.run/Popen argv; never run anything real."""
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: Any) -> Any:
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="gaia 0.1.0a1\n", stderr="")

    def fake_popen(argv: list[str], **_kw: Any) -> Any:
        seen.append(argv)
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    return seen


def test_update_runs_uv_pip_upgrade(
    calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    (constants.HOME_DIR / "venv").mkdir(parents=True)  # the venv must exist
    monkeypatch.setattr("gaia.cli._pidfile.PidFile.read_live", lambda self: None)  # daemon down
    # No --ref → pin to the latest release (offline-safe: mock the lookup, never hit GitHub).
    monkeypatch.setattr(lifecycle, "_latest_release_tag", lambda: "v0.1.0a1")

    monkeypatch.setattr("gaia.mcp._resolve_runtime", lambda _n: "/opt/bunx")  # bunx present

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    pip = next(c for c in calls if c[:3] == ["uv", "pip", "install"])
    assert "--upgrade" in pip and pip[-1] == f"gaia[all] @ git+{lifecycle.REPO}@v0.1.0a1"
    assert not any(c[-1:] == ["restart"] for c in calls)  # daemon down → no restart
    # update also repairs the runtime deps (the playwright-mcp browser) — #303 self-heal
    assert any(isinstance(c, list) and "install-browser" in c for c in calls)


def test_update_restarts_running_daemon(
    calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    (constants.HOME_DIR / "venv").mkdir(parents=True)
    monkeypatch.setattr("gaia.cli._pidfile.PidFile.read_live", lambda self: 999)  # daemon up

    result = runner.invoke(app, ["update", "--ref", "dev"])
    assert result.exit_code == 0, result.output
    pip = next(c for c in calls if c[:3] == ["uv", "pip", "install"])
    assert pip[-1] == f"gaia[all] @ git+{lifecycle.REPO}@dev"
    assert any(c[-1] == "restart" for c in calls)  # restarted to apply


def test_update_without_venv_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["update"])  # no ~/.gaia/venv (tmp home is empty)
    assert result.exit_code == 1 and "no gaia venv" in result.output


def _shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shim = tmp_path / "bin" / "gaia"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\n")
    monkeypatch.setattr(lifecycle, "_shim", lambda: shim)  # never touch the real ~/.local/bin/gaia
    return shim


def test_uninstall_keeps_data_by_default(
    calls: list[list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = _shim(tmp_path, monkeypatch)
    constants.HOME_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True if "Remove gaia" in a[0] else False)

    result = runner.invoke(app, ["uninstall"])
    assert result.exit_code == 0, result.output
    assert not shim.exists()  # shim removed
    rm = next(c for c in calls if c[0] == "sh")  # the detached cleanup
    assert str(constants.HOME_DIR / "venv") in rm[-1]  # removes the venv only
    assert str(constants.HOME_DIR) not in rm[-1].replace(str(constants.HOME_DIR / "venv"), "")
    assert "data stays" in result.output


def test_uninstall_purge_removes_home(
    calls: list[list[str]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shim(tmp_path, monkeypatch)
    constants.HOME_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["uninstall", "--purge"])
    assert result.exit_code == 0, result.output
    rm = next(c for c in calls if c[0] == "sh")
    assert str(constants.HOME_DIR) in rm[-1]  # the whole home is wiped


# --- latest-release lookup (the install-default source) ----------------------------


class _FakeResp:
    """A urlopen result that's both a context manager and json.load-readable."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, *_a: object) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, resp: object) -> None:
    import urllib.request

    def fake(*_a: object, **_k: object) -> object:
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_latest_release_tag_parses_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, _FakeResp(b'[{"tag_name": "v0.1.0a1"}, {"tag_name": "v0.0.9"}]'))
    assert lifecycle._latest_release_tag() == "v0.1.0a1"  # newest first, incl. prerelease


def test_latest_release_tag_none_when_no_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, _FakeResp(b"[]"))
    assert lifecycle._latest_release_tag() is None  # → caller falls back to master


def test_latest_release_tag_none_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, OSError("no network"))
    assert lifecycle._latest_release_tag() is None

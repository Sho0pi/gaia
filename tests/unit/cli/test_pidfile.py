"""PidFile: read/write/alive/read_live lifecycle with fake pids (no module state)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from godpy.cli._pidfile import PidFile


@pytest.fixture
def pidfile(tmp_path: Path) -> PidFile:
    return PidFile(tmp_path / "godpy.pid")


def test_write_read_round_trip(pidfile: PidFile) -> None:
    pidfile.write()
    assert pidfile.read() == os.getpid()
    assert pidfile.path.read_text().strip() == str(os.getpid())

    pidfile.write(12345)
    assert pidfile.read() == 12345


def test_read_missing_or_garbage(pidfile: PidFile) -> None:
    assert pidfile.read() is None  # missing
    pidfile.path.write_text("not-a-pid\n")
    assert pidfile.read() is None  # garbage


def test_alive_own_pid() -> None:
    assert PidFile.alive(os.getpid()) is True


def test_alive_dead_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("godpy.cli._pidfile.os.kill", raise_lookup)
    assert PidFile.alive(99999) is False


def test_alive_permission_error_means_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_permission(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr("godpy.cli._pidfile.os.kill", raise_permission)
    assert PidFile.alive(1) is True


def test_read_live_returns_live_pid(pidfile: PidFile) -> None:
    pidfile.write()  # our own pid: definitely alive

    assert pidfile.read_live() == os.getpid()
    assert pidfile.path.exists()


def test_read_live_removes_stale_file(pidfile: PidFile, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile.write(99999)
    monkeypatch.setattr(PidFile, "alive", staticmethod(lambda pid: False))

    assert pidfile.read_live() is None
    assert not pidfile.path.exists()  # stale file cleaned eagerly


def test_read_live_removes_garbage_file(pidfile: PidFile) -> None:
    pidfile.path.write_text("garbage\n")

    assert pidfile.read_live() is None
    assert not pidfile.path.exists()


def test_remove_idempotent(pidfile: PidFile) -> None:
    pidfile.remove()  # missing: no error
    pidfile.write()
    pidfile.remove()
    pidfile.remove()  # double remove: no error
    assert not pidfile.path.exists()


def test_default_path_comes_from_constants() -> None:
    from godpy import constants

    assert PidFile().path == constants.PID_FILE

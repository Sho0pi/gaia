"""Pidfile helpers: read/write/alive/read_live lifecycle with fake pids."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from godpy.cli import _pidfile


@pytest.fixture(autouse=True)
def pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "godpy.pid"
    monkeypatch.setattr(_pidfile, "PID_FILE", path)
    return path


def test_write_read_round_trip(pid_file: Path) -> None:
    _pidfile.write()
    assert _pidfile.read() == os.getpid()
    assert pid_file.read_text().strip() == str(os.getpid())

    _pidfile.write(12345)
    assert _pidfile.read() == 12345


def test_read_missing_or_garbage(pid_file: Path) -> None:
    assert _pidfile.read() is None  # missing
    pid_file.write_text("not-a-pid\n")
    assert _pidfile.read() is None  # garbage


def test_alive_own_pid() -> None:
    assert _pidfile.alive(os.getpid()) is True


def test_alive_dead_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(_pidfile.os, "kill", raise_lookup)
    assert _pidfile.alive(99999) is False


def test_alive_permission_error_means_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_permission(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(_pidfile.os, "kill", raise_permission)
    assert _pidfile.alive(1) is True


def test_read_live_returns_live_pid(pid_file: Path) -> None:
    _pidfile.write()  # our own pid: definitely alive

    assert _pidfile.read_live() == os.getpid()
    assert pid_file.exists()


def test_read_live_removes_stale_file(pid_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pidfile.write(99999)
    monkeypatch.setattr(_pidfile, "alive", lambda pid: False)

    assert _pidfile.read_live() is None
    assert not pid_file.exists()  # stale file cleaned eagerly


def test_read_live_removes_garbage_file(pid_file: Path) -> None:
    pid_file.write_text("garbage\n")

    assert _pidfile.read_live() is None
    assert not pid_file.exists()


def test_remove_idempotent(pid_file: Path) -> None:
    _pidfile.remove()  # missing: no error
    _pidfile.write()
    _pidfile.remove()
    _pidfile.remove()  # double remove: no error
    assert not pid_file.exists()

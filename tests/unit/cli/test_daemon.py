"""Daemon CLI commands: serve/start/stop/restart/status with fake processes.

Everything process-shaped is faked: ``Popen`` is a stub whose construction writes the
pidfile (mimicking the child's startup commit), ``os.kill`` records signals, and
``gaia.app.run_daemon`` is patched (commands import it lazily, so patching the
``gaia.app`` attribute intercepts the call).
"""

from __future__ import annotations

import json
import signal
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.cli import daemon
from gaia.cli._pidfile import PidFile
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gaia.pid"
    monkeypatch.setattr("gaia.constants.PID_FILE", path)  # PidFile() default
    return path


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Real Settings pointed at a tmp config + log dir; patched into the light imports."""
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text("connectors:\n  telegram:\n    enabled: true\n")
    settings = Settings(config_path=config_path, log_dir=tmp_path / "logs")
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    return settings


def _disable_background(settings: Settings) -> None:
    settings.config_path.write_text("connectors: {}\n")


class _FakePopen:
    """Stands in for the spawned serve process; writes the pidfile like the child would."""

    instances: ClassVar[list[_FakePopen]] = []
    write_pidfile: ClassVar[bool] = True

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv = argv
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode: int | None = None
        _FakePopen.instances.append(self)
        if _FakePopen.write_pidfile:
            PidFile().write(self.pid)

    def poll(self) -> int | None:
        return self.returncode


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> type[_FakePopen]:
    _FakePopen.instances = []
    _FakePopen.write_pidfile = True
    monkeypatch.setattr(daemon.subprocess, "Popen", _FakePopen)
    return _FakePopen


# --- serve -------------------------------------------------------------------------


def test_serve_refuses_when_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PidFile, "read_live", lambda self: 1234)
    called: list[Any] = []
    monkeypatch.setattr("gaia.app.run_daemon", lambda **kw: called.append(kw) or 0)

    result = runner.invoke(cli_app, ["serve"])

    assert result.exit_code == daemon.EXIT_DAEMON
    assert "already running" in result.output
    assert not called


@pytest.mark.parametrize("code", [0, 1])
def test_serve_exit_mirrors_run_daemon(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        "gaia.app.run_daemon",
        lambda settings=None, *, env_file=None, hold=False: (
            called.update(env_file=env_file, hold=hold) or code
        ),
    )

    result = runner.invoke(cli_app, ["serve"])

    assert result.exit_code == code
    assert called == {"env_file": None, "hold": False}


def test_serve_forwards_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        "gaia.app.run_daemon",
        lambda settings=None, *, env_file=None, hold=False: called.update(hold=hold) or 0,
    )

    result = runner.invoke(cli_app, ["serve", "--hold"])

    assert result.exit_code == 0
    assert called == {"hold": True}


# --- start -------------------------------------------------------------------------


def test_start_refuses_when_already_running(
    settings: Settings, fake_popen: type[_FakePopen], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(PidFile, "read_live", lambda self: 1234)

    result = runner.invoke(cli_app, ["start"])

    assert result.exit_code == daemon.EXIT_DAEMON
    assert not fake_popen.instances  # never spawned


def test_start_allows_socket_only_daemon(settings: Settings, fake_popen: type[_FakePopen]) -> None:
    _disable_background(settings)

    result = runner.invoke(cli_app, ["start"])

    assert result.exit_code == 0
    assert "started (pid 4242)" in result.output
    assert fake_popen.instances


def test_start_spawns_and_confirms_via_pidfile(
    settings: Settings, fake_popen: type[_FakePopen], tmp_path: Path
) -> None:
    env = tmp_path / "custom.env"
    result = runner.invoke(cli_app, ["--env-file", str(env), "start"])

    assert result.exit_code == 0
    assert "started (pid 4242)" in result.output
    (proc,) = fake_popen.instances
    assert proc.argv[-1] == "serve"
    assert "--env-file" in proc.argv  # global flag forwarded to the child
    assert proc.argv.index("--env-file") < proc.argv.index("serve")  # before the subcommand
    assert proc.kwargs["start_new_session"] is True


def test_start_reports_early_crash_with_log_tail(
    settings: Settings, fake_popen: type[_FakePopen], monkeypatch: pytest.MonkeyPatch
) -> None:
    log = settings.log_dir / "daemon.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("Traceback: boom\n")

    def crashed_init(self: _FakePopen, argv: list[str], **kwargs: Any) -> None:
        # Dies before committing startup: returncode set, no pidfile written.
        self.argv, self.kwargs, self.pid, self.returncode = argv, kwargs, 4242, 1
        _FakePopen.instances.append(self)

    monkeypatch.setattr(_FakePopen, "__init__", crashed_init)  # restored after the test

    result = runner.invoke(cli_app, ["start"])

    assert result.exit_code == 1
    assert "exited immediately" in result.output
    assert "boom" in result.output  # daemon.log tail surfaced


# --- stop / restart ----------------------------------------------------------------


def test_stop_not_running(settings: Settings) -> None:
    result = runner.invoke(cli_app, ["stop"])

    assert result.exit_code == daemon.EXIT_DAEMON
    assert "not running" in result.output


def test_stop_graceful_sigterm(
    settings: Settings, pid_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    PidFile().write(4242)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    alive_polls = iter([True, False])  # read_live sees it alive, first wait-poll sees it dead
    monkeypatch.setattr(PidFile, "alive", staticmethod(lambda pid: next(alive_polls)))

    result = runner.invoke(cli_app, ["stop"])

    assert result.exit_code == 0
    assert sent == [(4242, signal.SIGTERM)]  # no SIGKILL on the graceful path
    assert not pid_file.exists()


def test_stop_falls_back_to_sigkill(
    settings: Settings, pid_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    PidFile().write(4242)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(PidFile, "alive", staticmethod(lambda pid: True))  # never dies on its own

    result = runner.invoke(cli_app, ["stop", "--timeout", "0"])

    assert result.exit_code == 0
    assert (4242, signal.SIGKILL) in sent
    assert "SIGKILL" in result.output
    assert not pid_file.exists()  # parent removes it for the killed child


def test_restart_tolerates_not_running(settings: Settings, fake_popen: type[_FakePopen]) -> None:
    result = runner.invoke(cli_app, ["restart"])

    assert result.exit_code == 0
    assert "was not running" in result.output
    assert len(fake_popen.instances) == 1  # still started


# --- status ------------------------------------------------------------------------


def test_status_running_json(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    PidFile().write(4242)
    monkeypatch.setattr(PidFile, "alive", staticmethod(lambda pid: True))

    result = runner.invoke(cli_app, ["--json", "status"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["running"] is True
    assert data["pid"] == 4242
    assert data["uptime_seconds"] >= 0
    assert data["connectors"] == ["telegram"]
    assert set(data["logs"]) == {"daemon", "system", "errors", "events"}


def test_status_not_running(settings: Settings) -> None:
    result = runner.invoke(cli_app, ["status"])

    assert result.exit_code == daemon.EXIT_DAEMON
    assert "not running" in result.output


def test_status_cleans_stale_pidfile(
    settings: Settings, pid_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    PidFile().write(99999)
    monkeypatch.setattr(PidFile, "alive", staticmethod(lambda pid: False))

    result = runner.invoke(cli_app, ["status"])

    assert result.exit_code == daemon.EXIT_DAEMON
    assert not pid_file.exists()

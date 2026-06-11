"""System: real daemon round-trip — serve --hold, status, SIGTERM stop, pidfile gone.

Runs in a fully isolated ``HOME`` (godpy's HOME_DIR derives from ``Path.home()``), so
the real ``~/.godpy`` is never touched. ``--hold`` keeps serve resident with zero
connectors; no model key or network is needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.system

_BOOT_WAIT = 30.0  # ADK import is slow on first spawn
_POLL = 0.2


def _cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "godpy.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_daemon_round_trip(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path), "GEMINI_API_KEY": "dummy"}
    home = tmp_path / ".godpy"
    pid_file = home / "godpy.pid"
    log_path = tmp_path / "daemon.log"

    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "godpy.cli", "serve", "--hold"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + _BOOT_WAIT
        while time.monotonic() < deadline and not pid_file.exists():
            assert proc.poll() is None, (
                f"serve exited early (code {proc.returncode}):\n{log_path.read_text()}"
            )
            time.sleep(_POLL)
        assert pid_file.exists(), f"pidfile never appeared:\n{log_path.read_text()}"
        assert int(pid_file.read_text().strip()) == proc.pid

        status = _cli(["status"], env)
        assert status.returncode == 0, status.stdout + status.stderr
        assert "running" in status.stdout

        stop = _cli(["stop"], env)
        assert stop.returncode == 0, stop.stdout + stop.stderr

        proc.wait(timeout=15)
        assert not pid_file.exists()  # serve's finally removed it

        status_after = _cli(["status"], env)
        assert status_after.returncode == 3  # daemon-state exit code

        # Graceful-path evidence: the SIGTERM handler logged before shutdown.
        assert "received SIGTERM" in log_path.read_text()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

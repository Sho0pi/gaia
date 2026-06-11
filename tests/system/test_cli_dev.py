"""System: ``godpy dev`` boots and serves the ADK web UI on a port."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_dev_serves_http() -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "godpy.cli", "dev", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"godpy dev exited early with code {proc.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    assert resp.status < 500
                    return
            except urllib.error.HTTPError:
                return  # any HTTP response means the server is up
            except Exception as exc:  # connection refused while booting
                last_err = exc
                time.sleep(0.5)
        pytest.fail(f"dev UI never came up: {last_err}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

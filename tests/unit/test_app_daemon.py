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


def test_run_dev_scaffolds_the_commented_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A fresh home gets the documented gaia.yaml even when `gaia dev` is the first command (#55).
    cfg = tmp_path / "gaia.yaml"
    monkeypatch.setattr(
        app, "Gaia", lambda settings: SimpleNamespace(config=SimpleNamespace(logging=None))
    )
    monkeypatch.setattr(app, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("gaia.dev.serve_dev", lambda *a, **k: None)

    assert not cfg.exists()
    app.run_dev(Settings(config_path=cfg, log_dir=tmp_path / "logs"))
    assert cfg.exists() and "# " in cfg.read_text()  # the commented scaffold landed


async def _boom(*_a: Any, **_k: Any) -> None:
    raise RuntimeError("kaboom in serve")


def test_run_daemon_captures_a_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A fatal failure in _serve → a redacted crash report is written and the error re-raised.
    fake = SimpleNamespace(config=SimpleNamespace(logging=None))
    monkeypatch.setattr(app, "Gaia", lambda s: fake)
    monkeypatch.setattr(app, "write_default_config", lambda path: None)
    monkeypatch.setattr(app, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(app, "plan_launch", lambda cfg, daemon=False: [])
    monkeypatch.setattr(app, "_serve", _boom)

    with pytest.raises(RuntimeError, match="kaboom"):
        app.run_daemon(_settings(tmp_path))

    from gaia.crash import recent_crashes

    crashes = recent_crashes()
    assert crashes and "kaboom in serve" in crashes[-1].read_text()


def test_shutdown_watchdog_is_daemon_and_force_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    # The watchdog must be a daemon timer (so it never blocks exit itself) whose action is os._exit.
    exited: list[int] = []
    monkeypatch.setattr(app.os, "_exit", lambda code: exited.append(code))

    timer = app._arm_shutdown_watchdog(grace=999)
    try:
        assert timer.daemon is True
        assert timer.interval == 999
        timer.function()  # invoke the timer's action directly
        assert exited == [0]  # force-exits with 0
    finally:
        timer.cancel()


def test_shutdown_watchdog_fires_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    fired = threading.Event()
    monkeypatch.setattr(app.os, "_exit", lambda _code: fired.set())

    app._arm_shutdown_watchdog(grace=0.05)
    assert fired.wait(timeout=2.0)  # the timer actually fires on its own


def test_build_mem0_disables_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from types import ModuleType, SimpleNamespace

    monkeypatch.delenv("MEM0_TELEMETRY", raising=False)
    monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)
    fake = ModuleType("mem0")
    fake.Memory = SimpleNamespace(from_config=lambda cfg: "mem0")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mem0", fake)

    from gaia.config import Settings
    from gaia.config.schema import MemoryConfig
    from gaia.memory.backend import build_mem0

    build_mem0(Settings(), MemoryConfig())
    import os

    assert os.environ["MEM0_TELEMETRY"] == "false"
    assert os.environ["ANONYMIZED_TELEMETRY"] == "False"  # chromadb's own telemetry, off (#297)


def test_run_until_complete_does_not_join_default_executor() -> None:
    # neonize runs its blocking Go call via asyncio.to_thread (the default executor); asyncio.run
    # force-joins that on close and hangs on Linux. _run_until_complete must NOT wait for it (#300).
    import asyncio
    import threading
    import time

    started = threading.Event()
    release = threading.Event()

    def block() -> None:
        started.set()
        release.wait(5)  # a wedged worker: a join would stall here for up to 5s

    async def main() -> None:
        asyncio.get_running_loop().run_in_executor(None, block)  # fire-and-forget on default pool
        started.wait(2)  # make sure the worker is running before the loop tears down

    t0 = time.monotonic()
    app._run_until_complete(main())
    elapsed = time.monotonic() - t0
    release.set()  # let the orphaned worker finish (so the test process exits clean)

    assert elapsed < 2.0  # returned promptly; did NOT join the busy executor worker


async def test_startup_sweep_consolidates_only_stale_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consolidate_idle_sessions digests + clears sessions idle past the threshold; keeps fresh."""
    import time

    consolidated: list[str] = []
    deleted: list[str] = []
    now = time.time()
    stale = SimpleNamespace(id="u:cli", last_update_time=now - 99999, events=[object()])
    fresh = SimpleNamespace(id="u:tg", last_update_time=now, events=[object()])
    by_id = {"u:cli": stale, "u:tg": fresh}

    async def add_session_to_memory(session: Any) -> None:
        consolidated.append(session.id)

    async def list_sessions(**_kw: Any) -> Any:
        return SimpleNamespace(sessions=[stale, fresh])  # metadata carries last_update_time

    async def get_session(*, session_id: str, **_kw: Any) -> Any:
        return by_id[session_id]

    async def delete_session(*, session_id: str, **_kw: Any) -> None:
        deleted.append(session_id)

    gaia = SimpleNamespace(
        memory_service=SimpleNamespace(add_session_to_memory=add_session_to_memory),
        session_service=SimpleNamespace(
            list_sessions=list_sessions, get_session=get_session, delete_session=delete_session
        ),
        config=SimpleNamespace(
            memory=SimpleNamespace(auto_ingest=True),
            sessions=SimpleNamespace(idle_consolidate_minutes=30.0),
        ),
        users=SimpleNamespace(list=lambda: [SimpleNamespace(id="u")]),
    )

    await app._consolidate_idle_sessions(gaia)

    assert consolidated == ["u:cli"] and deleted == ["u:cli"]  # stale digested + cleared
    # fresh one (within idle window) left alone

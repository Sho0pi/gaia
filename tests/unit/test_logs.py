"""Unit tests for the logging system (system logs + structured events + redaction)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import godpy.logs as logs_module
from godpy.config import Settings
from godpy.config.schema import LoggingConfig
from godpy.logs import log_event, setup_logging


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Undo the global logger mutations setup_logging makes, so tests stay isolated."""
    yield
    for name in ("godpy", "godpy.events"):
        log = logging.getLogger(name)
        for handler in list(log.handlers):
            handler.close()
        log.handlers.clear()
        log.propagate = True
        log.setLevel(logging.NOTSET)
    logs_module._configured = False


def _setup(tmp_path: Path, **settings_kwargs: object) -> Path:
    return setup_logging(Settings(log_dir=tmp_path, **settings_kwargs), LoggingConfig(), force=True)


def test_creates_three_files_and_returns_dir(tmp_path: Path) -> None:
    log_dir = _setup(tmp_path)

    logging.getLogger("godpy.test").warning("boom")
    log_event("tool_used", tool="web_search")

    assert log_dir == tmp_path
    assert (tmp_path / "system.log").exists()
    assert (tmp_path / "errors.log").exists()
    assert (tmp_path / "events.jsonl").exists()


def test_warning_reaches_errors_log(tmp_path: Path) -> None:
    _setup(tmp_path)

    logging.getLogger("godpy.connectors").warning("whatsapp failed to load")

    assert "whatsapp failed to load" in (tmp_path / "errors.log").read_text()


def test_event_is_json_and_isolated_from_system_log(tmp_path: Path) -> None:
    _setup(tmp_path)

    log_event("tool_used", tool="web_search", user="u1")

    line = (tmp_path / "events.jsonl").read_text().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["message"] == "tool_used"
    assert record["tool"] == "web_search"
    assert record["user"] == "u1"
    # Events must not leak into the system log (propagate=False).
    assert "tool_used" not in (tmp_path / "system.log").read_text()


def test_secrets_are_redacted_before_disk(tmp_path: Path) -> None:
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"  # telegram-shaped
    _setup(tmp_path, GODPY_TELEGRAM_BOT_TOKEN=token)

    logging.getLogger("godpy").error("connecting with token=%s", token)

    content = (tmp_path / "errors.log").read_text()
    assert token not in content
    assert "***REDACTED***" in content


def test_setup_is_idempotent(tmp_path: Path) -> None:
    _setup(tmp_path)
    system = logging.getLogger("godpy")
    count = len(system.handlers)

    # Without force, a second call is a no-op (no duplicated handlers).
    setup_logging(Settings(log_dir=tmp_path), LoggingConfig())

    assert len(system.handlers) == count

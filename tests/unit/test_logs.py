"""Unit tests for the logging system (system logs + structured events + redaction)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

import godpy.logs as logs_module
from godpy.config import Settings
from godpy.config.schema import LoggingConfig
from godpy.logs import ConsoleFormatter, _supports_color, log_event, setup_logging


def _record(name: str, level: str, msg: str, **fields: object) -> logging.LogRecord:
    record = logging.LogRecord(name, getattr(logging, level), "", 0, msg, None, None)
    record.__dict__.update(fields)
    return record


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Undo the global logger mutations setup_logging makes, so tests stay isolated."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
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


def test_reserved_field_name_does_not_crash(tmp_path: Path) -> None:
    _setup(tmp_path)

    # 'created' is a reserved LogRecord attribute; logging it must not raise.
    log_event("tool_used", tool="delegate_to_soul", created=True)

    record = json.loads((tmp_path / "events.jsonl").read_text().strip().splitlines()[-1])
    assert record["message"] == "tool_used"
    assert record["created_"] is True  # suffixed to dodge the reserved name


def test_secrets_are_redacted_before_disk(tmp_path: Path) -> None:
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"  # telegram-shaped
    _setup(tmp_path, GODPY_TELEGRAM_BOT_TOKEN=token)

    logging.getLogger("godpy").error("connecting with token=%s", token)

    content = (tmp_path / "errors.log").read_text()
    assert token not in content
    assert "***REDACTED***" in content


def test_third_party_logs_are_captured_to_file(tmp_path: Path) -> None:
    _setup(tmp_path)

    # google_adk / google_genai etc. log under their own tree -> must still reach our
    # files via the root handlers (not just ADK's screen-only basicConfig handler).
    logging.getLogger("google_adk.models.google_llm").info("Sending out request")

    assert "Sending out request" in (tmp_path / "system.log").read_text()


def test_adk_basicconfig_after_setup_is_noop(tmp_path: Path) -> None:
    _setup(tmp_path)
    root = logging.getLogger()
    count = len(root.handlers)

    # Once root has handlers, ADK's later basicConfig must not add another (no dup line).
    logging.basicConfig(level=logging.INFO, format="[%(name)s %(levelname)s]")

    assert len(root.handlers) == count


def test_console_formatter_plain_when_color_off() -> None:
    fmt = ConsoleFormatter(color=False)

    line = fmt.format(_record("godpy.god.handler", "INFO", "built root agent"))

    assert "\033[" not in line  # no ANSI
    assert "INFO" in line and "god.handler" in line and "built root agent" in line


def test_console_formatter_colors_when_on() -> None:
    fmt = ConsoleFormatter(color=True)

    line = fmt.format(_record("godpy", "ERROR", "boom"))

    assert "\033[" in line  # ANSI present
    assert "boom" in line


def test_event_formatter_renders_action_and_fields() -> None:
    fmt = ConsoleFormatter(color=False, event=True)

    line = fmt.format(_record("godpy.events", "INFO", "tool_used", tool="web_search", results=5))

    assert "▸ tool_used" in line
    assert "tool=web_search" in line and "results=5" in line


def test_supports_color_honours_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    tty = SimpleNamespace(isatty=lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert _supports_color(tty) is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert _supports_color(tty) is False


def test_supports_color_false_for_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _supports_color(SimpleNamespace(isatty=lambda: False)) is False


def test_setup_is_idempotent(tmp_path: Path) -> None:
    _setup(tmp_path)
    root = logging.getLogger()
    count = len(root.handlers)

    # Without force, a second call is a no-op (no duplicated handlers).
    setup_logging(Settings(log_dir=tmp_path), LoggingConfig())

    assert len(root.handlers) == count

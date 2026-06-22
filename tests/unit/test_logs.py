"""Unit tests for the logging system (system logs + structured events + redaction)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from gaia.config import Settings
from gaia.config.schema import LoggingConfig
from gaia.logs import ConsoleFormatter, log_event, setup_logging


def _record(name: str, level: str, msg: str, **fields: object) -> logging.LogRecord:
    record = logging.LogRecord(name, getattr(logging, level), "", 0, msg, None, None)
    record.__dict__.update(fields)
    return record


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Undo the logger mutations setup_logging makes, so tests stay isolated.

    Detection of prior setup is now handler-presence based (see
    ``logs._already_configured``), so clearing the root + gaia handlers is the
    whole reset — no module-level flag to flip.
    """
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
    for name in ("gaia", "gaia.events"):
        log = logging.getLogger(name)
        for handler in list(log.handlers):
            handler.close()
        log.handlers.clear()
        log.propagate = True
        log.setLevel(logging.NOTSET)


def _setup(tmp_path: Path, **settings_kwargs: object) -> Path:
    return setup_logging(Settings(log_dir=tmp_path, **settings_kwargs), LoggingConfig(), force=True)


def test_creates_three_files_and_returns_dir(tmp_path: Path) -> None:
    log_dir = _setup(tmp_path)

    logging.getLogger("gaia.test").warning("boom")
    log_event("tool_used", tool="web_search")

    assert log_dir == tmp_path
    assert (tmp_path / "system.log").exists()
    assert (tmp_path / "errors.log").exists()
    assert (tmp_path / "events.jsonl").exists()


def test_warning_reaches_errors_log(tmp_path: Path) -> None:
    _setup(tmp_path)

    logging.getLogger("gaia.connectors").warning("whatsapp failed to load")

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
    _setup(tmp_path, GAIA_TELEGRAM_BOT_TOKEN=token)

    logging.getLogger("gaia").error("connecting with token=%s", token)

    content = (tmp_path / "errors.log").read_text()
    assert token not in content
    assert "***REDACTED***" in content


def test_openai_key_redacted_even_without_sk_prefix(tmp_path: Path) -> None:
    # Exact-match redaction must not rely on the generic 'sk-' pattern.
    key = "legacyOpenAiKeyWithoutPrefix123456"
    _setup(tmp_path, OPENAI_API_KEY=key)

    logging.getLogger("gaia").error("openai auth failed for key=%s", key)

    content = (tmp_path / "errors.log").read_text()
    assert key not in content
    assert "***REDACTED***" in content


def test_console_off_keeps_files_but_no_stdout_handlers(tmp_path: Path) -> None:
    # TUI mode: Textual owns the terminal, so no handler may write to stdout —
    # yet every stream must still reach its rotating file.
    setup_logging(Settings(log_dir=tmp_path), LoggingConfig(), force=True, console=False)

    def stream_handlers(logger: logging.Logger) -> list[logging.Handler]:
        return [
            h
            for h in logger.handlers
            if type(h) is logging.StreamHandler  # RotatingFileHandler subclasses it
        ]

    assert stream_handlers(logging.getLogger()) == []
    assert stream_handlers(logging.getLogger("gaia.events")) == []

    logging.getLogger("gaia").warning("tui boom")
    log_event("tool_used", tool="web_search")

    assert "tui boom" in (tmp_path / "system.log").read_text()
    assert "tool_used" in (tmp_path / "events.jsonl").read_text()


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

    line = fmt.format(_record("gaia.core.handler", "INFO", "built root agent"))

    assert "\033[" not in line  # no ANSI
    # gocat-style: dim time, level badge ("I"), the gaia-stripped logger name as the tag, message.
    assert "core.handler" in line and "built root agent" in line


def test_console_formatter_colors_when_on() -> None:
    fmt = ConsoleFormatter(color=True)

    line = fmt.format(_record("gaia", "ERROR", "boom"))

    assert "\033[" in line  # ANSI present
    assert "boom" in line


def test_event_formatter_tag_is_agent_with_action_and_fields() -> None:
    fmt = ConsoleFormatter(color=False, event=True)

    line = fmt.format(
        _record("gaia.events", "INFO", "tool_used", agent="frontend_developer", tool="fs_write")
    )

    # The agent is the tag; the action is the body; agent is pulled out of the fields.
    assert "frontend_developer" in line and "tool_used" in line
    assert "tool=fs_write" in line and "agent=" not in line


def test_event_formatter_folds_project_into_the_tag() -> None:
    fmt = ConsoleFormatter(color=False, event=True)

    line = fmt.format(
        _record("gaia.events", "INFO", "tool_used", agent="frontend_developer", project="pasta")
    )

    assert "frontend_developer/pasta" in line and "project=" not in line


def test_event_formatter_shows_agent_on_every_line() -> None:
    fmt = ConsoleFormatter(color=False, event=True)

    first = fmt.format(_record("gaia.events", "INFO", "tool_call", agent="gaia"))
    second = fmt.format(_record("gaia.events", "INFO", "tool_used", agent="gaia"))

    assert "gaia" in first and "gaia" in second  # agent shown on every line, never blanked


def test_setup_is_idempotent(tmp_path: Path) -> None:
    _setup(tmp_path)
    root = logging.getLogger()
    count = len(root.handlers)

    # Without force, a second call is a no-op (no duplicated handlers).
    setup_logging(Settings(log_dir=tmp_path), LoggingConfig())

    assert len(root.handlers) == count


def test_setup_reruns_when_root_handlers_cleared(tmp_path: Path) -> None:
    """No module-level flag: detection is purely handler-presence-based.

    If something nukes the root handlers between calls, the next ``setup_logging``
    re-runs (instead of skipping because of a stale "already configured" flag).
    """
    _setup(tmp_path)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    setup_logging(Settings(log_dir=tmp_path), LoggingConfig())

    assert len(root.handlers) > 0


def test_force_rebuilds_handlers(tmp_path: Path) -> None:
    _setup(tmp_path)
    before = list(logging.getLogger().handlers)

    setup_logging(Settings(log_dir=tmp_path), LoggingConfig(), force=True)
    after = list(logging.getLogger().handlers)

    # Same shape, but distinct handler instances — force closed + replaced them.
    assert len(after) == len(before)
    assert all(b is not a for b, a in zip(before, after, strict=False))

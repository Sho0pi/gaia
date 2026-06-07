"""Logging setup — system logs + structured user-activity ("event") logs.

Modelled on hermes-agent: stdlib :mod:`logging`, rotating files, secret redaction
before disk, noisy third-party loggers muted. Two streams:

* **system** — logger ``godpy`` (and its ``godpy.*`` children): operational messages.
  Handlers: console + ``system.log`` (INFO+) + ``errors.log`` (WARNING+).
* **events** — logger ``godpy.events``: structured user activity for a future analyzer
  agent. Handlers: console (human) + ``events.jsonl`` (one JSON object per line).
  ``propagate=False`` so events do not also land in the system files.

Everything streams to the screen *and* to rotating files under ``settings.log_dir``
(default ``~/.godpy/logs``). Use :func:`log_event` for user activity; everywhere else
use a plain ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pythonjsonlogger.json import JsonFormatter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.config.schema import LoggingConfig
    from godpy.config.settings import Settings

Redactor = Callable[[str], str]

_SYSTEM_LOGGER = "godpy"
_EVENTS_LOGGER = "godpy.events"
_REDACTED = "***REDACTED***"

# Third-party loggers that are noisy at INFO; pinned to WARNING.
_NOISY = ("httpx", "httpcore", "google", "grpc", "urllib3", "telegram", "neonize", "asyncio")

# Standard LogRecord attributes — anything else on a record is a user-supplied field.
_STD_ATTRS = frozenset(
    logging.makeLogRecord({}).__dict__.keys() | {"message", "asctime", "taskName"}
)

# Token-shaped secrets to scrub even when the exact value is unknown.
_GENERIC_SECRETS = (
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),  # telegram bot token
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # openai-style key
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),  # google api key
)

_configured = False


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """User-supplied fields on a record (everything that is not a standard attr)."""
    return {
        k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS and not k.startswith("_")
    }


def _build_redactor(settings: Settings) -> Redactor:
    """Return a function that strips secrets from a formatted log line."""
    exact = [
        re.escape(v)
        for v in (settings.telegram_bot_token, settings.whatsapp_token, settings.google_api_key)
        if v
    ]
    exact_re = re.compile("|".join(exact)) if exact else None

    def redact(text: str) -> str:
        if exact_re is not None:
            text = exact_re.sub(_REDACTED, text)
        for pattern in _GENERIC_SECRETS:
            text = pattern.sub(_REDACTED, text)
        return text

    return redact


class _RedactMixin(logging.Formatter):
    """Formatter mixin that scrubs secrets from the final formatted string.

    Subclasses ``Formatter`` so the cooperative ``super().format`` resolves through
    the MRO (to ``JsonFormatter`` or ``Formatter``) and type-checks cleanly.
    """

    def __init__(self, *args: Any, redactor: Redactor | None = None, **kwargs: Any) -> None:
        self._redactor = redactor
        super().__init__(*args, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return self._redactor(text) if self._redactor else text


class RedactingFormatter(_RedactMixin, logging.Formatter):
    """Plain text formatter with redaction."""


class RedactingJsonFormatter(_RedactMixin, JsonFormatter):
    """JSON-lines formatter with redaction (for events.jsonl)."""


class EventConsoleFormatter(logging.Formatter):
    """Human-readable mirror of an event: ``event: <action> k=v …``."""

    def __init__(self, *args: Any, redactor: Redactor | None = None, **kwargs: Any) -> None:
        self._redactor = redactor
        super().__init__(*args, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = _extra_fields(record)
        if fields:
            base += " " + " ".join(f"{k}={v}" for k, v in fields.items())
        return self._redactor(base) if self._redactor else base


def _rotating(
    path: Path, level: int, formatter: logging.Formatter, cfg: LoggingConfig
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path, maxBytes=cfg.max_size_mb * 1024 * 1024, backupCount=cfg.backup_count
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def setup_logging(settings: Settings, cfg: LoggingConfig, *, force: bool = False) -> Path:
    """Configure logging once. Returns the log directory. Idempotent unless ``force``."""
    global _configured
    log_dir = Path(settings.log_dir)
    if _configured and not force:
        return log_dir

    log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.getLevelNamesMapping().get(cfg.level.upper(), logging.INFO)
    redactor = _build_redactor(settings)

    text_fmt = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", redactor=redactor
    )

    # System logger: console + system.log (INFO+) + errors.log (WARNING+).
    system = logging.getLogger(_SYSTEM_LOGGER)
    system.setLevel(level)
    system.handlers.clear()
    system.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(text_fmt)
    system.addHandler(console)
    system.addHandler(_rotating(log_dir / "system.log", level, text_fmt, cfg))
    system.addHandler(_rotating(log_dir / "errors.log", logging.WARNING, text_fmt, cfg))

    # Events logger: console (human) + events.jsonl (machine). No propagation.
    events = logging.getLogger(_EVENTS_LOGGER)
    events.setLevel(logging.INFO)
    events.handlers.clear()
    events.propagate = False

    events_console = logging.StreamHandler(sys.stdout)
    events_console.setLevel(logging.INFO)
    events_console.setFormatter(
        EventConsoleFormatter("%(asctime)s %(levelname)s event: %(message)s", redactor=redactor)
    )
    events.addHandler(events_console)
    events.addHandler(
        _rotating(
            log_dir / "events.jsonl",
            logging.INFO,
            RedactingJsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s", redactor=redactor
            ),
            cfg,
        )
    )

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    return log_dir


def log_event(action: str, **fields: Any) -> None:
    """Record a structured user-activity event (message in/out, tool used, …).

    ``action`` is the event name; ``fields`` are structured key/values written verbatim
    to ``events.jsonl`` and mirrored to the console. Keep secrets out of ``fields`` —
    redaction is best-effort, not a guarantee.
    """
    logging.getLogger(_EVENTS_LOGGER).info(action, extra=fields)

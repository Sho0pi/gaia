"""Logging setup — system logs + structured user-activity ("event") logs.

Modelled on hermes-agent: stdlib :mod:`logging`, rotating files, secret redaction
before disk, noisy third-party loggers muted. Two streams:

* **system** — logger ``gaia`` (and its ``gaia.*`` children): operational messages.
  Handlers: console + ``system.log`` (INFO+) + ``errors.log`` (WARNING+).
* **events** — logger ``gaia.events``: structured user activity for a future analyzer
  agent. Handlers: console (human) + ``events.jsonl`` (one JSON object per line).
  ``propagate=False`` so events do not also land in the system files.

Everything streams to the screen *and* to rotating files under ``settings.log_dir``
(default ``~/.gaia/logs``) — except in TUI mode, where the console handlers are
skipped (``console=False``) because Textual owns the terminal. Use :func:`log_event`
for user activity; everywhere else use a plain ``logging.getLogger(__name__)``.
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

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config.schema import LoggingConfig
    from gaia.config.settings import Settings

Redactor = Callable[[str], str]

_REDACTED = "***REDACTED***"

# Transport/library loggers that are noisy at INFO; pinned to WARNING. (google_adk /
# google_genai are deliberately left at INFO so model request/response stay visible.)
_NOISY = ("httpx", "httpcore", "urllib3", "grpc", "asyncio", "neonize", "telegram")

# Loggers muted below ERROR — ADK's OTel metrics emitter warns on every non-Gemini
# turn ("Skipping missing token usage metadata", harmless: OpenAI reports usage in a
# shape ADK's meter doesn't read). Telemetry itself is off (see _TELEMETRY_OFF); this
# just hides the residual log line. mem0's spaCy loader warns twice per ingest when the
# optional ``mem0ai[nlp]`` extra isn't installed — harmless fallback, pure noise.
_MUTED = ("google_adk.google.adk.telemetry", "mem0.utils.spacy_models")

# Standard LogRecord attributes — anything else on a record is a user-supplied field.
_STD_ATTRS = frozenset(
    logging.makeLogRecord({}).__dict__.keys() | {"message", "asctime", "taskName"}
)

# Token-shaped secrets to scrub even when the exact value is unknown.
_GENERIC_SECRETS = (
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),  # telegram bot token
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # openai-style key
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),  # google api key
    re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT (oauth)
)

# Logging setup is process-bootstrap — a side-effect on the root logger
# (handlers installed, levels set), not a service with an instance to cache.
# A lazy singleton needs a *value* to hold; there is none here. The "have we
# already configured logging?" bit therefore lives on the right object — the
# handler we own, on the root logger — instead of a parallel module-level
# flag. That's why this is a marker attribute and not a `providers.Singleton`
# or a module-level `_configured` boolean: no out-of-band state, no test
# fixture reset, and the bit and the thing it describes share one lifetime.
_HANDLER_MARK = "_gaia_owned"


def _mark(handler: logging.Handler) -> logging.Handler:
    """Tag ``handler`` so :func:`_already_configured` recognises our prior setup."""
    setattr(handler, _HANDLER_MARK, True)
    return handler


def _already_configured() -> bool:
    """True if a previous :func:`setup_logging` call has installed handlers."""
    root = logging.getLogger()
    return any(getattr(h, _HANDLER_MARK, False) for h in root.handlers)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """User-supplied fields on a record (everything that is not a standard attr)."""
    return {
        k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS and not k.startswith("_")
    }


def _build_redactor(settings: Settings) -> Redactor:
    """Return a function that strips secrets from a formatted log line."""
    exact = [
        re.escape(v)
        for v in (
            settings.telegram_bot_token,
            settings.whatsapp_token,
            settings.google_api_key,
            settings.openai_api_key,
        )
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


class ConsoleFormatter(logging.Formatter):
    """gocat-style console output for the system and event streams (rendered by :mod:`gaia.logfmt`).

    The actor tag is the soul ``agent[/project]`` (run-scoped contextvars for system logs, the
    explicit field for events) so every line shows who it belongs to; the module is the logger
    name (system) or the action (events). Redaction runs last, on the final string.
    """

    def __init__(
        self, *, redactor: Redactor | None = None, color: bool, event: bool = False
    ) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self._redactor = redactor
        self._color = color
        self._event = event

    def format(self, record: logging.LogRecord) -> str:
        from gaia.logfmt import render_line
        from gaia.tools.fs.base import current_agent, current_project

        ts = self.formatTime(record, self.datefmt)
        # The actor tag (agent[/project]) on EVERY line: events carry it explicitly; system logs
        # read the run-scoped contextvars (a soul run sets them; default is the root "gaia").
        if self._event:
            fields = _extra_fields(record)
            agent = fields.pop("agent", None) or current_agent.get()
            project = fields.pop("project", None) or (current_project.get() or None)
            error = fields.get("status") == "error"
            module = record.getMessage()  # the action (tool_used, message_in, …)
            body = ""
            str_fields: dict[str, Any] | None = {k: str(v) for k, v in fields.items()} or None
        else:
            agent = current_agent.get()
            project = current_project.get() or None
            error = False
            module = record.name.removeprefix("gaia.")
            body = record.getMessage()
            if record.exc_info:
                body = f"{body}\n{self.formatException(record.exc_info)}"
            str_fields = None

        tag = f"{agent}/{project}" if project else str(agent)
        line = render_line(
            ts=ts,
            tag=tag,
            level=record.levelname,
            body=body,
            module=module,
            fields=str_fields,
            color=self._color,
            error=error,
        )
        return self._redactor(line) if self._redactor else line


def _rotating(
    path: Path, level: int, formatter: logging.Formatter, cfg: LoggingConfig
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path, maxBytes=cfg.max_size_mb * 1024 * 1024, backupCount=cfg.backup_count
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def setup_logging(
    settings: Settings, cfg: LoggingConfig, *, force: bool = False, console: bool = True
) -> Path:
    """Configure logging once. Returns the log directory. Idempotent unless ``force``.

    ``console=False`` skips the stdout handlers and logs to the rotating files only.
    The foreground chat path needs this: a ``StreamHandler`` writing to stdout would
    interleave log lines with the prompt and Gaia replies.
    """
    log_dir = Path(settings.log_dir)
    if _already_configured() and not force:
        return log_dir

    log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.getLevelNamesMapping().get(cfg.level.upper(), logging.INFO)
    redactor = _build_redactor(settings)

    # Plain formatter for the rotating files (no ANSI ever lands on disk); colourised
    # formatter for the console, on only when stdout is a real terminal.
    text_fmt = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", redactor=redactor
    )
    from gaia.logfmt import supports_color

    color = supports_color(sys.stdout)

    # Own the ROOT logger so third-party libraries (google_adk, google_genai, …) flow
    # into our files + console with our format + redaction, instead of only hitting the
    # handler ADK installs via logging.basicConfig (screen-only). Once root has handlers,
    # that later basicConfig becomes a no-op. system.log is the catch-all; errors.log is
    # the WARNING+ subset.
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setLevel(level)
        stream.setFormatter(ConsoleFormatter(redactor=redactor, color=color))
        root.addHandler(_mark(stream))
    root.addHandler(_mark(_rotating(log_dir / "system.log", level, text_fmt, cfg)))
    root.addHandler(_mark(_rotating(log_dir / "errors.log", logging.WARNING, text_fmt, cfg)))

    # gaia logs inherit the root handlers (no dedicated handlers of their own).
    system = logging.getLogger(constants.LOGGER_NAME)
    system.handlers.clear()
    system.propagate = True
    system.setLevel(logging.NOTSET)

    # Events logger: console (human) + events.jsonl (machine). No propagation.
    events = logging.getLogger(constants.EVENTS_LOGGER_NAME)
    events.setLevel(logging.INFO)
    events.handlers.clear()
    events.propagate = False

    if console:
        events_console = logging.StreamHandler(sys.stdout)
        events_console.setLevel(logging.INFO)
        events_console.setFormatter(ConsoleFormatter(redactor=redactor, color=color, event=True))
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
    for name in _MUTED:
        logging.getLogger(name).setLevel(logging.ERROR)

    return log_dir


def log_event(action: str, *, exc: BaseException | None = None, **fields: Any) -> None:
    """Record a structured user-activity event (message in/out, tool used, …).

    ``action`` is the event name; ``fields`` are structured key/values written verbatim
    to ``events.jsonl`` and mirrored to the console. Keep secrets out of ``fields`` —
    redaction is best-effort, not a guarantee.

    Pass ``exc=`` to auto-fill ``error`` (type), ``detail`` (message) and ``where`` (the gaia
    frame) from an exception — so an error call site is just ``log_event("turn_error", user=…,
    exc=exc)`` instead of extracting them by hand. Explicitly-passed fields win over the extract.

    A field whose name collides with a reserved ``LogRecord`` attribute (e.g. ``created``,
    ``name``, ``module``) is suffixed with ``_`` rather than crashing ``logging`` — so a
    field name can never take down the caller (usually a tool mid-run).
    """
    if exc is not None:
        detail, where = error_details(exc)
        fields.setdefault("error", type(exc).__name__)
        fields.setdefault("detail", detail)
        if where:
            fields.setdefault("where", where)
    safe = {(f"{k}_" if k in _STD_ATTRS else k): v for k, v in fields.items()}
    logging.getLogger(constants.EVENTS_LOGGER_NAME).info(action, extra=safe)


def error_details(exc: BaseException) -> tuple[str, str]:
    """A short message + the deepest *gaia* frame (``file:line``) for a structured error event.

    The self-monitoring loop reads these off ``events.jsonl``, so an error event needs enough to
    triage without parsing ``errors.log`` tracebacks: ``str(exc)`` (truncated) and the innermost
    non-site-packages frame (our code, where it's actionable; falls back to the innermost frame).
    """
    import traceback

    detail = str(exc)[:300]
    frames = traceback.extract_tb(exc.__traceback__)
    where = ""
    for frame in reversed(frames):
        if "site-packages" not in frame.filename:
            where = f"{Path(frame.filename).name}:{frame.lineno}"
            break
    if not where and frames:
        last = frames[-1]
        where = f"{Path(last.filename).name}:{last.lineno}"
    return detail, where


def log_error(source: str, exc: BaseException, **fields: Any) -> None:
    """Emit a structured ``error`` event for a *background* failure (no live turn to report it).

    ``source`` is a logical location (e.g. ``"cron_runner"``, ``"monitor_loop"``); the event also
    carries the exception type, a truncated message, and the deepest gaia frame — so daemon/cron/
    loop failures show up in ``events.jsonl`` for the monitor, not only as ``system.log`` text.
    """
    log_event("error", source=source, exc=exc, **fields)

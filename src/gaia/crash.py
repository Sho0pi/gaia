"""Crash capture — write a redacted crash report when the daemon dies, for ``gaia report``.

Per-turn errors already flow to ``events.jsonl`` via :func:`gaia.logs.log_error` (the self-monitor
reads them). A **fatal** crash propagates out of ``asyncio.run`` and would only leave a stderr
traceback; :func:`write_crash_report` captures it as a structured, secret-redacted JSON under
``~/.gaia/crashes`` so ``gaia report`` (and the monitor) can surface it.
"""

from __future__ import annotations

import json
import platform
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import Settings

_MARKER = ".reported"  # CRASHES_DIR/.reported holds the unix time of the last admin notice


def _redactor(settings: Settings | None) -> Any:
    if settings is None:
        return lambda text: text
    from gaia.logs import _build_redactor

    return _build_redactor(settings)


def write_crash_report(
    exc: BaseException, *, settings: Settings | None = None, context: dict[str, Any] | None = None
) -> Path:
    """Write a redacted crash report and return its path (best-effort)."""
    from gaia import __version__

    redact = _redactor(settings)
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    when = datetime.now(UTC)
    report = {
        "time": when.isoformat(timespec="seconds"),
        "gaia_version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "error": redact(f"{type(exc).__name__}: {exc}"),
        "traceback": redact(tb),
        **(context or {}),
    }
    constants.CRASHES_DIR.mkdir(parents=True, exist_ok=True)
    path = constants.CRASHES_DIR / f"{when.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def recent_crashes(*, since: float | None = None) -> list[Path]:
    """Crash report files (oldest first), optionally only those newer than ``since`` (mtime)."""
    if not constants.CRASHES_DIR.exists():
        return []
    files = sorted(constants.CRASHES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if since is not None:
        files = [f for f in files if f.stat().st_mtime > since]
    return files


def last_reported() -> float:
    """Unix time the admin was last notified about crashes (0 if never)."""
    marker = constants.CRASHES_DIR / _MARKER
    try:
        return float(marker.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def mark_reported() -> None:
    """Record that the admin was just notified, so the next restart doesn't re-notify."""
    constants.CRASHES_DIR.mkdir(parents=True, exist_ok=True)
    (constants.CRASHES_DIR / _MARKER).write_text(str(time.time()))

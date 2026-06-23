"""Shared result-dict builders for tools.

Tools return ``{"status": "success"|"error", ...}`` dicts and never raise to the
model (see CLAUDE.md). These two helpers are the single source of that shape so
every tool — fs, shell, browser, task, cron, … — agrees on the keys.
"""

from __future__ import annotations

from typing import Any


def err(message: str) -> dict[str, Any]:
    """A standard error result: ``{"status": "error", "error_message": message}``."""
    return {"status": "error", "error_message": message}


def ok(**fields: Any) -> dict[str, Any]:
    """A standard success result: ``{"status": "success", **fields}``."""
    return {"status": "success", **fields}

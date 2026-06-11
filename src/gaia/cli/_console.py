"""Shared rich console, ``--json`` emitter, and secret-masking helpers.

rich is imported lazily (inside function bodies) so building the command tree stays
stdlib+typer only — part of the ``gaia --help`` < 150 ms contract.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from rich.console import Console


@lru_cache(maxsize=1)
def console() -> Console:
    """The process-wide rich console (honors NO_COLOR, set early by ``--no-color``)."""
    from rich.console import Console

    return Console()


def emit_json(data: dict[str, Any]) -> None:
    """Print machine-readable output for ``--json`` consumers (plain stdout, no styling)."""
    print(json.dumps(data, indent=2, sort_keys=True))


def mask_secret(value: str, *, keep: int = 4) -> str:
    """Mask a secret for display, keeping only the last ``keep`` characters."""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * 8}{value[-keep:]}"

"""Dedup state for the monitor: don't report the same error signature every cycle.

ponytail: a flat ``~/.gaia/monitor_state.json`` of ``signature -> last-reported ISO``; a finding is
suppressed if it was reported within the cooldown. Fine for one daemon — move to the sqlite store if
it ever runs multi-process.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gaia import constants


def _path() -> Path:
    return constants.HOME_DIR / "monitor_state.json"


def _load() -> dict[str, str]:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def filter_new(signatures: list[str], cooldown_hours: float) -> list[str]:
    """Return signatures not reported within ``cooldown_hours``; record the returned ones as now."""
    state = _load()
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=cooldown_hours)
    fresh: list[str] = []
    for sig in signatures:
        last = state.get(sig)
        if last:
            try:
                if datetime.fromisoformat(last) > cutoff:
                    continue  # reported recently -> suppress
            except ValueError:
                pass
        fresh.append(sig)
        state[sig] = now.isoformat(timespec="seconds")
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    return fresh

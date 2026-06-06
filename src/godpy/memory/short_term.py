"""Short-term memory — the per-conversation scratchpad.

Thin wrapper over a key/value state dict, mirroring ADK ``Session.state``. Kept
deliberately small: anything worth keeping past the session is promoted to
:class:`~godpy.memory.long_term.LongTermMemory`.
"""

from __future__ import annotations

from typing import Any


class ShortTermMemory:
    """Volatile per-session state. Maps onto ADK ``Session.state``."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state: dict[str, Any] = dict(state) if state else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state[key] = value

    def as_dict(self) -> dict[str, Any]:
        return dict(self._state)

    def clear(self) -> None:
        self._state.clear()

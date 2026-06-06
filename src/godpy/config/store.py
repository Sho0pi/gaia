"""Hot-swappable supplier of :class:`~godpy.config.schema.GodConfig`.

``ConfigStore.current`` is the supplier: each access stats ``god.yaml`` and, only
when its mtime changed (or the file appeared/disappeared since last read), reparses
it. Edit the file and the next read sees the new value — no process restart. Readers
that pull config per use (e.g. per message) get hot reload for free.

A ``subscribe(cb)`` hook is provided for the few consumers that must *react* to a
change rather than poll (e.g. restarting a connector when it is toggled). It is not
wired to any reactive consumer yet — that lifecycle work is a follow-up (issue #10).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from godpy.config.schema import GodConfig
from godpy.config.settings import Settings

# Called with the freshly-loaded config whenever the file is (re)read.
Subscriber = Callable[[GodConfig], None]


class ConfigStore:
    """File-backed, mtime-gated supplier of the live :class:`GodConfig`."""

    def __init__(self, path: Path, settings: Settings) -> None:
        self._path = Path(path)
        self._settings = settings
        self._subs: list[Subscriber] = []
        self._mtime: float | None = None
        self._config: GodConfig = self._reload()

    @property
    def current(self) -> GodConfig:
        """Return the live config, reparsing only if the file changed on disk."""
        mtime = self._stat_mtime()
        if mtime != self._mtime:
            self._config = self._reload()
            for cb in self._subs:
                cb(self._config)
        return self._config

    def subscribe(self, cb: Subscriber) -> None:
        """Register ``cb`` to be called with the new config on every reload."""
        self._subs.append(cb)

    def _stat_mtime(self) -> float | None:
        """Modification time of the config file, or ``None`` when it is absent."""
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return None

    def _reload(self) -> GodConfig:
        """Parse the YAML (missing file -> defaults) and merge env-held secrets.

        The new config is built fully before being returned/assigned, so a reader
        racing with a reload never observes a half-applied config.
        """
        self._mtime = self._stat_mtime()
        raw: dict[str, object] = {}
        if self._mtime is not None:
            loaded = yaml.safe_load(self._path.read_text()) or {}
            if isinstance(loaded, dict):
                raw = loaded

        config = GodConfig.model_validate(raw)
        # Env always wins over file for secrets: the YAML should leave token blank.
        if self._settings.telegram_bot_token:
            config.connectors.telegram.token = self._settings.telegram_bot_token
        return config

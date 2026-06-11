"""Hot-swappable supplier of :class:`~gaia.config.schema.GaiaConfig`.

``ConfigSupplier.current`` is the supplier: each access checks ``gaia.yaml``'s
modification time (``mtime``) and reparses the file *only* when it changed since the
last read — "mtime-gated". The file is otherwise not touched, so reads are cheap. Net
effect: edit ``gaia.yaml`` and the next ``.current`` sees the new value, no process
restart. Callers that pull config per use (e.g. once per message) get hot reload for
free.

A ``subscribe(cb)`` hook is provided for the few consumers that must *react* to a
change rather than poll. It is not wired to any reactive consumer yet — that
lifecycle work is a follow-up (issue #10).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from gaia.config.schema import GaiaConfig

# Called with the freshly-loaded config whenever the file is (re)read.
Subscriber = Callable[[GaiaConfig], None]


class ConfigSupplier:
    """File-backed, mtime-gated supplier of the live :class:`GaiaConfig`."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._subs: list[Subscriber] = []
        self._mtime: float | None = None
        self._config: GaiaConfig = self._reload()

    @property
    def current(self) -> GaiaConfig:
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

    def _reload(self) -> GaiaConfig:
        """Parse the YAML (missing file -> defaults).

        The new config is built fully before being returned/assigned, so a reader
        racing with a reload never observes a half-applied config.
        """
        self._mtime = self._stat_mtime()
        raw: dict[str, object] = {}
        if self._mtime is not None:
            loaded = yaml.safe_load(self._path.read_text())
            # safe_load returns whatever the document's top level is — None for an
            # empty file, or a str/list if someone writes a bare scalar/sequence.
            # GaiaConfig.model_validate needs a mapping, so anything else -> defaults.
            if isinstance(loaded, dict):
                raw = loaded

        return GaiaConfig.model_validate(raw)

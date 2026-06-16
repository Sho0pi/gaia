"""Local static-serve tools: serve a soul's built site over loopback http to render it."""

from __future__ import annotations

from gaia.tools.serve.base import (
    DEFAULT_IDLE_SECONDS,
    ServedPorts,
    ServedSite,
    ServeError,
    StaticServerManager,
)
from gaia.tools.serve.serve import (
    SERVE,
    SERVE_LIST,
    SERVE_STOP,
    make_serve,
    make_serve_list,
    make_serve_stop,
)

__all__ = [
    "DEFAULT_IDLE_SECONDS",
    "SERVE",
    "SERVE_LIST",
    "SERVE_STOP",
    "ServeError",
    "ServedPorts",
    "ServedSite",
    "StaticServerManager",
    "make_serve",
    "make_serve_list",
    "make_serve_stop",
]

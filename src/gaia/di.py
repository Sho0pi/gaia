"""Composition root for Gaia's build-once services.

:class:`Container` wires the lazy-singleton services every :class:`Gaia` reuses
(transcriber, memory service, mcp/skill toolsets) via ``providers.Singleton`` —
built on first ``.X()`` call, reused after, scoped to one ``Gaia`` instance so
each test gets a fresh container. See ``CLAUDE.md`` → *Service lifecycle & DI*.

``settings`` and ``config_supplier`` are injected by ``Gaia.__init__`` (the DI
seam). ``config`` is a ``providers.Callable``, **not** a singleton, so each
access re-reads ``ConfigSupplier.current`` — yaml hot-reload still works.

:class:`LifecycleManager` is the cleanup hub. Each Singleton factory whose
result owns an async resource (mcp toolsets, skill toolsets) registers its
async closer at build time; ``Gaia.close()`` awaits
``container.lifecycle().aclose()`` once. A service that is never pulled
registers no closer, so the call is a free no-op — no need to introspect
private ``providers.Singleton`` storage to know what was built.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from dependency_injector import containers, providers

from gaia.skills import build_skill_toolset, resolve_skills_dir
from gaia.voice import build_transcriber

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.base_toolset import BaseToolset
    from google.adk.tools.mcp_tool import McpToolset

    from gaia.config import GaiaConfig, Settings
    from gaia.config.store import ConfigSupplier
    from gaia.memory import Mem0MemoryService
    from gaia.voice import Transcriber

logger = logging.getLogger(__name__)

AsyncCloser = Callable[[], Awaitable[None]]


class LifecycleManager:
    """Collects async closers for build-once services; awaited by Gaia.close().

    Each container factory that owns an async-cleanup resource registers a
    closer on first build via :meth:`add`. If the resource is never pulled,
    nothing registers and :meth:`aclose` is a no-op — that gives us
    "tear down only what was built" without poking ``providers.Singleton``
    internal state.
    """

    def __init__(self) -> None:
        self._closers: list[AsyncCloser] = []

    def add(self, closer: AsyncCloser) -> None:
        self._closers.append(closer)

    async def aclose(self) -> None:
        """Run every registered closer, swallowing per-closer failures.

        Shutdown is best-effort: one stuck toolset cannot block another
        toolset from teardown, and we never propagate to ``Gaia.close()``
        (which itself is best-effort).
        """
        for closer in self._closers:
            try:
                await closer()
            except Exception:  # pragma: no cover - shutdown best-effort
                logger.debug("lifecycle close failed", exc_info=True)


def _build_memory_service(settings: Settings, config: GaiaConfig) -> Mem0MemoryService:
    """Build the mem0-backed memory service. Caller gates on ``config.memory.enabled``."""
    from gaia.memory import Mem0MemoryService, build_mem0

    backend = build_mem0(settings, config.memory)
    return Mem0MemoryService(backend, recall_limit=config.memory.recall_limit)


def _build_mcp_toolsets(config: GaiaConfig, lifecycle: LifecycleManager) -> list[McpToolset]:
    """The MCP toolsets, plus playwright-mcp when ``browser.backend`` resolves to ``mcp``.

    Registers the async ``close_mcp_toolsets`` call with ``lifecycle`` so
    ``Gaia.close()`` tears down the stdio child processes on shutdown.
    """
    from gaia.config.schema import MCPConfig
    from gaia.mcp import (
        build_mcp_toolsets,
        close_mcp_toolsets,
        playwright_mcp_server,
        resolve_browser_backend,
    )

    servers = list(config.mcp.servers)
    if resolve_browser_backend(config.browser) == "mcp" and not any(
        s.name == "playwright" for s in servers
    ):
        servers.append(playwright_mcp_server(config.browser))
    toolsets = build_mcp_toolsets(MCPConfig(servers=servers))
    if toolsets:
        lifecycle.add(lambda: close_mcp_toolsets(toolsets))
    return toolsets


def _build_skill_toolsets(config: GaiaConfig, lifecycle: LifecycleManager) -> list[BaseToolset]:
    """Wrap the on-demand skills toolset in a list (``[]`` when none).

    Registers each toolset's async ``close`` with ``lifecycle`` so the
    skill loader's resources are released on shutdown.
    """
    toolset = build_skill_toolset(resolve_skills_dir(config))
    if toolset is None:
        return []

    async def _close() -> None:
        await toolset.close()

    lifecycle.add(_close)
    return [toolset]


class Container(containers.DeclarativeContainer):
    """Per-``Gaia`` DI container — lazy singletons for build-once services.

    Inject ``settings`` and ``config_supplier`` via constructor kwargs
    (``Container(settings=providers.Object(s), config_supplier=...)``). Each
    ``providers.Singleton`` is built on first call and reused thereafter; the
    container itself is single-use, so a fresh ``Gaia`` (or a fresh test)
    yields fresh singletons.
    """

    settings: providers.Dependency[Settings] = providers.Dependency()
    config_supplier: providers.Dependency[ConfigSupplier] = providers.Dependency()

    config: providers.Callable[GaiaConfig] = providers.Callable(
        lambda supplier: supplier.current, config_supplier
    )

    lifecycle: providers.Singleton[LifecycleManager] = providers.Singleton(LifecycleManager)

    transcriber: providers.Singleton[Transcriber | None] = providers.Singleton(
        build_transcriber, config
    )
    memory_service: providers.Singleton[Mem0MemoryService] = providers.Singleton(
        _build_memory_service, settings, config
    )
    mcp_toolsets: providers.Singleton[list[McpToolset]] = providers.Singleton(
        _build_mcp_toolsets, config, lifecycle
    )
    skill_toolsets: providers.Singleton[list[BaseToolset]] = providers.Singleton(
        _build_skill_toolsets, config, lifecycle
    )

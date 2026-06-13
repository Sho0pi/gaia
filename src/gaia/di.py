"""Composition root for Gaia's build-once services.

:class:`Container` wires the lazy-singleton services every :class:`Gaia` reuses
(transcriber, memory service, mcp/skill toolsets) via ``providers.Singleton`` —
built on first ``.X()`` call, reused after, scoped to one ``Gaia`` instance so
each test gets a fresh container. See ``CLAUDE.md`` → *Service lifecycle & DI*.

``settings`` and ``config_supplier`` are injected by ``Gaia.__init__`` (the DI
seam). ``config`` is a ``providers.Callable``, **not** a singleton, so each
access re-reads ``ConfigSupplier.current`` — yaml hot-reload still works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dependency_injector import containers, providers

from gaia.skills import build_skill_toolset, resolve_skills_dir
from gaia.voice import build_transcriber

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.base_toolset import BaseToolset
    from google.adk.tools.mcp_tool import McpToolset

    from gaia.config import GaiaConfig, Settings
    from gaia.config.store import ConfigSupplier
    from gaia.memory import Mem0MemoryService


def _current_config(supplier: ConfigSupplier) -> GaiaConfig:
    return supplier.current


def _agent_registry_dir(settings: Settings) -> object:
    return settings.agent_registry_dir


def _build_memory_service(settings: Settings, config: GaiaConfig) -> Mem0MemoryService:
    """Build the mem0-backed memory service. Caller gates on ``config.memory.enabled``."""
    from gaia.memory import Mem0MemoryService, build_mem0

    backend = build_mem0(settings, config.memory)
    return Mem0MemoryService(backend, recall_limit=config.memory.recall_limit)


def _build_mcp_toolsets(config: GaiaConfig) -> list[McpToolset]:
    """The MCP toolsets, plus playwright-mcp when ``browser.backend`` resolves to ``mcp``."""
    from gaia.config.schema import MCPConfig
    from gaia.mcp import build_mcp_toolsets, playwright_mcp_server, resolve_browser_backend

    servers = list(config.mcp.servers)
    if resolve_browser_backend(config.browser) == "mcp" and not any(
        s.name == "playwright" for s in servers
    ):
        servers.append(playwright_mcp_server(config.browser))
    return build_mcp_toolsets(MCPConfig(servers=servers))


def _build_skill_toolsets(config: GaiaConfig) -> list[BaseToolset]:
    """Wrap the on-demand skills toolset in a list (``[]`` when none)."""
    toolset = build_skill_toolset(resolve_skills_dir(config))
    return [toolset] if toolset is not None else []


class Container(containers.DeclarativeContainer):
    """Per-``Gaia`` DI container — lazy singletons for build-once services.

    Inject ``settings`` and ``config_supplier`` via constructor kwargs
    (``Container(settings=providers.Object(s), config_supplier=...)``). Each
    ``providers.Singleton`` is built on first call and reused thereafter; the
    container itself is single-use, so a fresh ``Gaia`` (or a fresh test)
    yields fresh singletons.
    """

    settings: Any = providers.Dependency()
    config_supplier: Any = providers.Dependency()

    config: Any = providers.Callable(_current_config, config_supplier)

    transcriber: Any = providers.Singleton(build_transcriber, config)
    memory_service: Any = providers.Singleton(_build_memory_service, settings, config)
    mcp_toolsets: Any = providers.Singleton(_build_mcp_toolsets, config)
    skill_toolsets: Any = providers.Singleton(_build_skill_toolsets, config)

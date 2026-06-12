"""Gaia: the root orchestrator.

Gaia owns the factory, registry and memory. For a task it decides which subagent
should handle it, reusing a stored agent when one already fits. The concrete ADK
root-agent wiring is built lazily in :meth:`Gaia.build_root_agent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gaia.agents import AgentFactory, AgentSpec, SoulRegistry
from gaia.communication import apply_communication_style
from gaia.config import ConfigSupplier, Settings, configure_adk_env, get_settings
from gaia.config.schema import AgentBinding
from gaia.models import resolve_model
from gaia.skills import attach_skills, resolve_skills_dir
from gaia.tools import default_registry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent
    from google.adk.tools.mcp_tool import McpToolset

    from gaia.config import GaiaConfig
    from gaia.memory import Mem0MemoryService


class Gaia:
    """Top-level agent that spawns, stores and reuses task-specific subagents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        configure_adk_env(self.settings)
        self.config_supplier = ConfigSupplier(self.settings.config_path)
        self.skills_dir = resolve_skills_dir(self.config)
        self.souls = SoulRegistry(self.settings.agent_registry_dir)
        self.tools = default_registry(self.config)
        self.factory = AgentFactory(
            self.souls,
            default_model=self.config.llm.model or self.settings.model,
            default_provider=self.config.llm.provider,
            default_use_oauth=self.config.llm.openai.use_oauth,
            skills_dir=self.skills_dir,
            default_communication_style=self.config.default_communication_style,
            tool_registry=self.tools,
            mcp_toolsets_provider=self.mcp_toolsets,
        )
        self._memory_service: Mem0MemoryService | None = None
        self._mcp: list[McpToolset] | None = None
        self._closed = False

    def mcp_toolsets(self) -> list[McpToolset]:
        """The configured external MCP toolsets, built once and shared by root + souls.

        Built lazily (the ADK/``mcp`` imports are deferred) so constructing Gaia needs
        neither; ``[]`` when no MCP server is configured. When the browser backend
        resolves to ``mcp``, Microsoft's playwright-mcp is appended as one more server
        (deduped if the user already configured one named ``playwright``).
        """
        if self._mcp is None:
            from gaia.config.schema import MCPConfig
            from gaia.mcp import (
                build_mcp_toolsets,
                playwright_mcp_server,
                resolve_browser_backend,
            )

            servers = list(self.config.mcp.servers)
            if resolve_browser_backend(self.config.browser) == "mcp" and not any(
                s.name == "playwright" for s in servers
            ):
                servers.append(playwright_mcp_server(self.config.browser))
            self._mcp = build_mcp_toolsets(MCPConfig(servers=servers))
        return self._mcp

    async def close(self) -> None:
        """Release every async resource on the *running* loop (idempotent, best-effort).

        Covers the stateful tool backends (shell processes, browser sessions) via the
        registry's ``aclose`` and the MCP stdio child processes. Called from each shutdown
        path while its loop is still alive, so nothing falls through to the tool managers'
        ``atexit`` hooks — which run after the loop is gone and raise 'Event loop is closed'.
        """
        if self._closed:
            return
        self._closed = True
        await self.tools.aclose()
        if self._mcp:
            from gaia.mcp import close_mcp_toolsets

            await close_mcp_toolsets(self._mcp)

    async def __aenter__(self) -> Gaia:
        """``async with Gaia(...):`` — :meth:`close` runs on exit, exceptions included."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def config(self) -> GaiaConfig:
        """The live, hot-reloaded ``gaia.yaml`` config (re-read on file change)."""
        return self.config_supplier.current

    @property
    def memory_service(self) -> Mem0MemoryService | None:
        """The long-term memory service (mem0), built once on first use.

        ``None`` when ``memory.enabled`` is false — Gaia then runs session-only, with no
        cross-session recall. Built lazily so importing/constructing Gaia needs no mem0
        backend or model key.
        """
        if not self.config.memory.enabled:
            return None
        if self._memory_service is None:
            from gaia.memory import Mem0MemoryService, build_mem0

            backend = build_mem0(self.settings, self.config.memory)
            self._memory_service = Mem0MemoryService(
                backend, recall_limit=self.config.memory.recall_limit
            )
        return self._memory_service

    def ensure_agent(self, spec: AgentSpec) -> LlmAgent:
        """Get a subagent for ``spec`` — reused if known, created+stored if new."""
        return self.factory.create_or_reuse(spec)

    def known_souls(self) -> list[str]:
        """Keys of every subagent Gaia has already learned."""
        return self.souls.list_keys()

    def build_root_agent(self) -> LlmAgent:
        """Construct the ADK root agent with all known subagents attached.

        Deferred ADK import keeps the rest of Gaia importable without a model.
        """
        from google.adk.agents import BaseAgent, LlmAgent

        sub_agents: list[BaseAgent] = [
            self.factory.create_or_reuse(self.souls.get(key))  # type: ignore[arg-type]
            for key in self.known_souls()
        ]
        base_instruction = (
            "You are Gaia. Answer simple questions yourself, calling your own tools when one "
            "fits rather than guessing. For a complex or creative build/creation task (e.g. "
            "designing a website, writing a program), call delegate_to_soul(task) — it finds "
            "the right specialist soul or forges a new one and runs it. When it returns, tell "
            "the user which soul handled it (say so explicitly when 'created' is true), then "
            "report the workspace path and the list of files the soul produced."
        )
        bound = self.config.agents.get("gaia", AgentBinding())
        instruction = attach_skills(base_instruction, bound.skills, self.skills_dir)
        style = bound.communication_style or self.config.default_communication_style
        instruction = apply_communication_style(instruction, style)

        from gaia.souls import make_delegate

        # delegate_to_soul is attached to the root only — souls (built from self.tools)
        # never receive it, so a soul cannot spawn souls.
        return LlmAgent(
            name="gaia",
            model=resolve_model(
                self.config.llm.model or self.settings.model,
                provider=self.config.llm.provider,
                use_oauth=self.config.llm.openai.use_oauth,
            ),
            description="Root orchestrator that routes tasks to specialized subagents.",
            instruction=instruction,
            tools=[*self.tools.all(), make_delegate(self), *self.mcp_toolsets()],
            sub_agents=sub_agents,
        )

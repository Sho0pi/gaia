"""Gaia: the root orchestrator.

Gaia owns the factory, registry and memory. For a task it decides which subagent
should handle it, reusing a stored agent when one already fits. The concrete ADK
root-agent wiring is built lazily in :meth:`Gaia.build_root_agent`.

Build-once services (transcriber, memory, mcp/skill toolsets) live in
:class:`gaia.di.Container` as lazy singletons; ``Gaia`` exposes them as
properties/methods that delegate to the container. See ``CLAUDE.md`` →
*Service lifecycle & DI*.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dependency_injector import providers

from gaia.agents import AgentFactory, AgentSpec, SoulRegistry
from gaia.communication import apply_communication_style
from gaia.config import ConfigSupplier, Settings, configure_adk_env, get_settings
from gaia.config.schema import AgentBinding
from gaia.di import Container
from gaia.models import resolve_model
from gaia.skills import attach_skills, resolve_skills_dir
from gaia.tools import default_registry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent
    from google.adk.tools.base_toolset import BaseToolset
    from google.adk.tools.mcp_tool import McpToolset

    from gaia.config import GaiaConfig
    from gaia.memory import Mem0MemoryService
    from gaia.voice import Transcriber


class Gaia:
    """Top-level agent that spawns, stores and reuses task-specific subagents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        configure_adk_env(self.settings)
        self.config_supplier = ConfigSupplier(self.settings.config_path)
        self.container = Container(
            settings=providers.Object(self.settings),
            config_supplier=providers.Object(self.config_supplier),
        )
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
            skill_toolset_provider=self.skill_toolsets,
        )
        # Cache provider results locally so close() can release them without
        # triggering build on a never-touched service (container would otherwise
        # construct an unused toolset just to tear it down).
        self._mcp: list[McpToolset] | None = None
        self._skill_toolsets: list[BaseToolset] | None = None
        self._closed = False

    def skill_toolsets(self) -> list[BaseToolset]:
        """The on-demand skills toolset (ADK SkillToolset), built once and shared.

        Lazy singleton: built on first call by ``gaia.di.Container`` (see
        ``CLAUDE.md`` → *Service lifecycle & DI*). Exposes every skill under
        ``skills_dir`` to the model via ``list_skills`` / ``load_skill``
        (progressive disclosure); ``[]`` when the folder holds no valid skill.
        """
        if self._skill_toolsets is None:
            self._skill_toolsets = self.container.skill_toolsets()
        return self._skill_toolsets

    def mcp_toolsets(self) -> list[McpToolset]:
        """The configured external MCP toolsets, built once and shared by root + souls.

        Lazy singleton via ``gaia.di.Container``. ``[]`` when no MCP server is
        configured. When the browser backend resolves to ``mcp``, Microsoft's
        playwright-mcp is appended as one more server (deduped if the user
        already configured one named ``playwright``).
        """
        if self._mcp is None:
            self._mcp = self.container.mcp_toolsets()
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
        for toolset in self._skill_toolsets or []:
            try:
                await toolset.close()
            except Exception:  # pragma: no cover - shutdown best-effort
                logger.debug("skill toolset close failed", exc_info=True)

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
        """The long-term memory service (mem0), a lazy singleton from the container.

        ``None`` when ``memory.enabled`` is false — Gaia then runs session-only,
        with no cross-session recall. Built on first access; subsequent calls
        return the cached instance even if ``memory.enabled`` is toggled, until
        Gaia is rebuilt.
        """
        if not self.config.memory.enabled:
            return None
        service: Mem0MemoryService = self.container.memory_service()
        return service

    @property
    def transcriber(self) -> Transcriber | None:
        """The voice-to-text transcriber, a lazy singleton from the container.

        Canonical "lazy singleton" example: built on first access (loads the
        Whisper model lazily inside its own ``transcribe()`` too), reused for
        every connector and tool that needs it. ``None`` when ``voice.enabled``
        is false or ``faster-whisper`` isn't installed.
        """
        result: Transcriber | None = self.container.transcriber()
        return result

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
        from datetime import datetime

        # Time-aware prompt (god PR #26): scheduled turns ("what day is it") and
        # cron-tool date math need the model to know now. Built per root-agent build;
        # the handler keeps the Runner (and thus this timestamp) for the session, so
        # it's approximate within a long-lived conversation — good enough for dates.
        now = datetime.now().strftime("%A, %Y-%m-%d %H:%M %Z").strip()
        base_instruction = (
            f"Current date and time: {now}.\n"
            "You are Gaia. Answer simple questions yourself, calling your own tools when one "
            "fits rather than guessing. For a complex or creative build/creation task (e.g. "
            "designing a website, writing a program), call delegate_to_soul(task) — it finds "
            "the right specialist soul or forges a new one and runs it. When it returns, tell "
            "the user which soul handled it (say so explicitly when 'created' is true), then "
            "report the workspace path and the list of files the soul produced. You can open "
            "those deliverables directly (fs_read takes the absolute paths under the souls' "
            "workspaces), so read/verify/summarize them yourself when the user asks. To "
            "schedule work for later or on a recurring basis (reminders, daily briefs), use "
            "the cron tool — it runs your message at the scheduled time and delivers the "
            "result to the user's chat."
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
            tools=[
                *self.tools.all(),
                make_delegate(self),
                *self.mcp_toolsets(),
                *self.skill_toolsets(),
            ],
            sub_agents=sub_agents,
        )

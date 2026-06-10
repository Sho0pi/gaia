"""God: the root orchestrator.

God owns the factory, registry and memory. For a task it decides which subagent
should handle it, reusing a stored agent when one already fits. The concrete ADK
root-agent wiring is built lazily in :meth:`God.build_root_agent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from godpy.agents import AgentFactory, AgentSpec, SoulRegistry
from godpy.communication import apply_communication_style
from godpy.config import ConfigSupplier, Settings, configure_adk_env, get_settings
from godpy.config.schema import AgentBinding
from godpy.models import resolve_model
from godpy.skills import attach_skills, resolve_skills_dir
from godpy.tools import default_registry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

    from godpy.config import GodConfig
    from godpy.memory import Mem0MemoryService


class God:
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
        )
        self._memory_service: Mem0MemoryService | None = None

    @property
    def config(self) -> GodConfig:
        """The live, hot-reloaded ``god.yaml`` config (re-read on file change)."""
        return self.config_supplier.current

    @property
    def memory_service(self) -> Mem0MemoryService | None:
        """The long-term memory service (mem0), built once on first use.

        ``None`` when ``memory.enabled`` is false — God then runs session-only, with no
        cross-session recall. Built lazily so importing/constructing God needs no mem0
        backend or model key.
        """
        if not self.config.memory.enabled:
            return None
        if self._memory_service is None:
            from godpy.memory import Mem0MemoryService, build_mem0

            backend = build_mem0(self.settings, self.config.memory)
            self._memory_service = Mem0MemoryService(
                backend, recall_limit=self.config.memory.recall_limit
            )
        return self._memory_service

    def ensure_agent(self, spec: AgentSpec) -> LlmAgent:
        """Get a subagent for ``spec`` — reused if known, created+stored if new."""
        return self.factory.create_or_reuse(spec)

    def known_souls(self) -> list[str]:
        """Keys of every subagent God has already learned."""
        return self.souls.list_keys()

    def build_root_agent(self) -> LlmAgent:
        """Construct the ADK root agent with all known subagents attached.

        Deferred ADK import keeps the rest of God importable without a model.
        """
        from google.adk.agents import BaseAgent, LlmAgent

        sub_agents: list[BaseAgent] = [
            self.factory.create_or_reuse(self.souls.get(key))  # type: ignore[arg-type]
            for key in self.known_souls()
        ]
        base_instruction = (
            "You are God. Answer simple questions yourself, calling your own tools when one "
            "fits rather than guessing. For a complex or creative build/creation task (e.g. "
            "designing a website, writing a program), call delegate_to_soul(task) — it finds "
            "the right specialist soul or forges a new one and runs it. When it returns, tell "
            "the user which soul handled it (say so explicitly when 'created' is true), then "
            "report the workspace path and the list of files the soul produced."
        )
        bound = self.config.agents.get("god", AgentBinding())
        instruction = attach_skills(base_instruction, bound.skills, self.skills_dir)
        style = bound.communication_style or self.config.default_communication_style
        instruction = apply_communication_style(instruction, style)

        from godpy.souls import make_delegate

        # delegate_to_soul is attached to the root only — souls (built from self.tools)
        # never receive it, so a soul cannot spawn souls.
        return LlmAgent(
            name="god",
            model=resolve_model(
                self.config.llm.model or self.settings.model,
                provider=self.config.llm.provider,
                use_oauth=self.config.llm.openai.use_oauth,
            ),
            description="Root orchestrator that routes tasks to specialized subagents.",
            instruction=instruction,
            tools=[*self.tools.all(), make_delegate(self)],
            sub_agents=sub_agents,
        )

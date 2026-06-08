"""God: the root orchestrator.

God owns the factory, registry and memory. For a task it decides which subagent
should handle it, reusing a stored agent when one already fits. The concrete ADK
root-agent wiring is built lazily in :meth:`God.build_root_agent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from godpy.agents import AgentFactory, AgentRegistry, AgentSpec
from godpy.communication import apply_communication_style
from godpy.config import ConfigSupplier, Settings, configure_adk_env, get_settings
from godpy.config.schema import AgentBinding
from godpy.memory import LongTermMemory, ShortTermMemory
from godpy.skills import attach_skills, resolve_skills_dir
from godpy.tools import default_registry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

    from godpy.config import GodConfig


class God:
    """Top-level agent that spawns, stores and reuses task-specific subagents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        configure_adk_env(self.settings)
        self.config_supplier = ConfigSupplier(self.settings.config_path)
        self.skills_dir = resolve_skills_dir(self.config)
        self.registry = AgentRegistry(self.settings.agent_registry_dir)
        self.tools = default_registry(self.config)
        self.factory = AgentFactory(
            self.registry,
            default_model=self.settings.model,
            skills_dir=self.skills_dir,
            default_communication_style=self.config.default_communication_style,
            tool_registry=self.tools,
        )
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory()

    @property
    def config(self) -> GodConfig:
        """The live, hot-reloaded ``god.yaml`` config (re-read on file change)."""
        return self.config_supplier.current

    def ensure_agent(self, spec: AgentSpec) -> LlmAgent:
        """Get a subagent for ``spec`` — reused if known, created+stored if new."""
        return self.factory.create_or_reuse(spec)

    def known_agents(self) -> list[str]:
        """Keys of every subagent God has already learned."""
        return self.registry.list_keys()

    def build_root_agent(self) -> LlmAgent:
        """Construct the ADK root agent with all known subagents attached.

        Deferred ADK import keeps the rest of God importable without a model.
        """
        from google.adk.agents import BaseAgent, LlmAgent

        sub_agents: list[BaseAgent] = [
            self.factory.create_or_reuse(self.registry.get(key))  # type: ignore[arg-type]
            for key in self.known_agents()
        ]
        base_instruction = (
            "You are God. Pick the subagent best suited to the user's task. "
            "If none fits, describe the new specialist needed so it can be created."
        )
        bound = self.config.agents.get("god", AgentBinding())
        instruction = attach_skills(base_instruction, bound.skills, self.skills_dir)
        style = bound.communication_style or self.config.default_communication_style
        instruction = apply_communication_style(instruction, style)

        return LlmAgent(
            name="god",
            model=self.config.llm.model or self.settings.model,
            description="Root orchestrator that routes tasks to specialized subagents.",
            instruction=instruction,
            tools=self.tools.resolve(bound.tools),
            sub_agents=sub_agents,
        )

"""God: the root orchestrator.

God owns the factory, registry and memory. For a task it decides which subagent
should handle it, reusing a stored agent when one already fits. The concrete ADK
root-agent wiring is built lazily in :meth:`God.build_root_agent`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from godpy.agents import AgentFactory, AgentRegistry, AgentSpec
from godpy.config import ConfigSupplier, Settings, configure_adk_env, get_settings
from godpy.memory import LongTermMemory, ShortTermMemory

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

    from godpy.config import GodConfig


class God:
    """Top-level agent that spawns, stores and reuses task-specific subagents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        configure_adk_env(self.settings)
        self.config_supplier = ConfigSupplier(self.settings.config_path)
        self.registry = AgentRegistry(self.settings.agent_registry_dir)
        self.factory = AgentFactory(self.registry, default_model=self.settings.model)
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
        return LlmAgent(
            name="god",
            model=self.config.llm.model or self.settings.model,
            description="Root orchestrator that routes tasks to specialized subagents.",
            instruction=(
                "You are God. Pick the subagent best suited to the user's task. "
                "If none fits, describe the new specialist needed so it can be created."
            ),
            sub_agents=sub_agents,
        )

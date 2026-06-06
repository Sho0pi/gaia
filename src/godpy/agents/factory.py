"""Build ADK subagents from a declarative spec, reusing stored ones when possible.

The ADK import is deferred into :meth:`AgentFactory._build_llm_agent` so the spec
and reuse logic stay unit-testable without a configured model backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from godpy.agents.registry import AgentRegistry
from godpy.agents.spec import AgentSpec, slugify

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent


class AgentFactory:
    """Creates subagents, but reuses a stored one whenever the key already exists."""

    def __init__(self, registry: AgentRegistry, *, default_model: str) -> None:
        self._registry = registry
        self._default_model = default_model

    def create_or_reuse(self, spec: AgentSpec) -> LlmAgent:
        """Return an ADK agent for ``spec``, loading from the registry if present.

        New specs are persisted so future tasks reuse them instead of recreating.
        """
        stored = self._registry.get(spec.key)
        if stored is not None:
            spec = stored
        else:
            self._registry.save(spec)
        return self._build_llm_agent(spec)

    def _build_llm_agent(self, spec: AgentSpec) -> LlmAgent:
        """Construct the concrete ADK agent. Imports ADK lazily on purpose."""
        from google.adk.agents import LlmAgent

        return LlmAgent(
            name=spec.key,
            model=spec.model or self._default_model,
            description=spec.description,
            instruction=spec.instruction,
        )


def to_agent_card(spec: AgentSpec, *, url: str = "") -> dict[str, Any]:
    """Render an :class:`AgentSpec` as an A2A AgentCard dict (schema v0.3)."""
    return {
        "name": spec.name,
        "description": spec.description,
        "url": url,
        "version": "0.1.0",
        "skills": [{"id": slugify(s), "name": s, "description": s} for s in spec.skills],
    }

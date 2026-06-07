"""Build ADK subagents from a declarative spec, reusing stored ones when possible.

The ADK import is deferred into :meth:`AgentFactory._build_llm_agent` so the spec
and reuse logic stay unit-testable without a configured model backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from godpy.agents.registry import AgentRegistry
from godpy.agents.spec import AgentSpec, slugify
from godpy.communication import DEFAULT_COMMUNICATION_STYLE, apply_communication_style
from godpy.skills import attach_skills, load_skill

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent


class AgentFactory:
    """Creates subagents, but reuses a stored one whenever the key already exists."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        default_model: str,
        skills_dir: Path | None = None,
        default_communication_style: str = DEFAULT_COMMUNICATION_STYLE,
    ) -> None:
        self._registry = registry
        self._default_model = default_model
        self._skills_dir = skills_dir
        self._default_communication_style = default_communication_style

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

        instruction = spec.instruction
        if self._skills_dir is not None:
            instruction = attach_skills(instruction, spec.skills, self._skills_dir)
        style = spec.communication_style or self._default_communication_style
        instruction = apply_communication_style(instruction, style)

        return LlmAgent(
            name=spec.key,
            model=spec.model or self._default_model,
            description=spec.description,
            instruction=instruction,
        )


def to_agent_card(
    spec: AgentSpec, *, url: str = "", skills_dir: Path | None = None
) -> dict[str, Any]:
    """Render an :class:`AgentSpec` as an A2A AgentCard dict (schema v0.3).

    When ``skills_dir`` is given, each skill id is resolved to its real
    name/description from the skill folder; otherwise the raw id is used for all
    three fields (cheap, registry-free behaviour).
    """
    skills = []
    for s in spec.skills:
        name, description = s, s
        if skills_dir is not None:
            skill = load_skill(skills_dir, s)
            if skill is not None:
                name, description = skill.frontmatter.name, skill.frontmatter.description
        skills.append({"id": slugify(s), "name": name, "description": description})
    return {
        "name": spec.name,
        "description": spec.description,
        "url": url,
        "version": "0.1.0",
        "skills": skills,
    }

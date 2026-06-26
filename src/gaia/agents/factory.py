"""Build ADK subagents from a declarative spec, reusing stored ones when possible.

The ADK import is deferred into :meth:`AgentFactory._build_llm_agent` so the spec
and reuse logic stay unit-testable without a configured model backend.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia.agents.registry import SoulRegistry
from gaia.agents.spec import AgentSpec, slugify
from gaia.communication import DEFAULT_COMMUNICATION_STYLE, apply_communication_style
from gaia.models import resolve_model, thinking_planner
from gaia.skills import attach_skills, load_skill

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

    from gaia.tools import ToolRegistry

#: Prepended to every soul's instruction so each forged-or-stored soul knows the two
#: delegation paths (P3). Kept here (not in the smith) so souls forged before P3 get it
#: without re-forging.
SOUL_PREAMBLE = """\
You are one specialist running a single task. Two ways to pull in other specialists:
- Need an ANSWER to keep going (a fact, a number, an opinion)? Call consult_soul(question)
  — the answer comes straight back to you in this turn.
- Need a DELIVERABLE produced (a separate piece of work)? Call task_create(title, spec) —
  it is filed as a subtask of yours; save your progress with task_update(notes=...), then
  STOP. You'll be re-run with the subtask's results once it's done.
Prefer consult for questions and subtasks for work. Don't call consult_soul in a loop.
Unsure what shell commands are allowed, or where you can write/serve? Call capabilities() first —
exec is allowlisted (one command, no &&/|/;) and fs + serve stay inside your workspace.

"""


class AgentFactory:
    """Creates subagents, but reuses a stored one whenever the key already exists."""

    def __init__(
        self,
        registry: SoulRegistry,
        *,
        default_model: str,
        default_provider: str = "gemini",
        default_use_oauth: bool = False,
        skills_dir: Path | None = None,
        default_communication_style: str = DEFAULT_COMMUNICATION_STYLE,
        tool_registry: ToolRegistry | None = None,
        mcp_toolsets_provider: Callable[[], list[Any]] | None = None,
        skill_toolset_provider: Callable[[], list[Any]] | None = None,
    ) -> None:
        self._registry = registry
        self._default_model = default_model
        self._default_provider = default_provider
        self._default_use_oauth = default_use_oauth
        self._skills_dir = skills_dir
        self._default_communication_style = default_communication_style
        self._tool_registry = tool_registry
        # Built lazily at agent-build time (where ADK is already imported); souls get the
        # same configured MCP toolsets and on-demand skills toolset as the root. Default: none.
        self._mcp_toolsets_provider = mcp_toolsets_provider or (lambda: [])
        self._skill_toolset_provider = skill_toolset_provider or (lambda: [])

    def create_or_reuse(
        self, spec: AgentSpec, *, effort: str = "", extra_tools: list[Any] | None = None
    ) -> LlmAgent:
        """Return an ADK agent for ``spec``, loading from the registry if present.

        New specs are persisted so future tasks reuse them instead of recreating.
        ``extra_tools`` are appended to the soul's tools at build time — the soul-run core
        passes ``consult_soul`` here (it needs the live ``gaia``, so it can't sit in the
        static registry and must be threaded in per build). ``effort`` is the live
        ``llm.effort`` (passed by the caller, which has the live config) so souls reason at the
        configured level without a restart.
        """
        stored = self._registry.get(spec.key)
        if stored is not None:
            spec = stored
        else:
            self._registry.save(spec)
        return self._build_llm_agent(spec, effort=effort, extra_tools=extra_tools)

    def _build_llm_agent(
        self, spec: AgentSpec, *, effort: str = "", extra_tools: list[Any] | None = None
    ) -> LlmAgent:
        """Construct the concrete ADK agent. Imports ADK lazily on purpose."""
        from google.adk.agents import LlmAgent

        instruction = SOUL_PREAMBLE + spec.instruction
        if self._skills_dir is not None:
            instruction = attach_skills(instruction, spec.skills, self._skills_dir)
        style = spec.communication_style or self._default_communication_style
        instruction = apply_communication_style(instruction, style)

        # Agents get every registered tool by default; a spec may pin a subset.
        tools: list[Any] = []
        if self._tool_registry is not None:
            tools = (
                self._tool_registry.resolve(spec.tools) if spec.tools else self._tool_registry.all()
            )
        # Configured external MCP servers attach to every soul too (not in the registry,
        # so AgentSpec.tools can't pin them — they're all-or-nothing per server config).
        tools = [
            *tools,
            *self._mcp_toolsets_provider(),
            *self._skill_toolset_provider(),
            *(extra_tools or []),
        ]

        model_id = spec.model or self._default_model
        return LlmAgent(
            name=spec.key,
            model=resolve_model(
                model_id,
                provider=self._default_provider,
                use_oauth=self._default_use_oauth,
                effort=effort,
            ),
            planner=thinking_planner(self._default_provider, model_id, effort),
            description=spec.description,
            instruction=instruction,
            tools=tools,
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

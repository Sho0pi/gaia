"""Factory reuse logic: a known spec is loaded from the registry, not recreated."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.agents import AgentFactory, AgentSpec, SoulRegistry
from gaia.agents.factory import to_agent_card
from gaia.communication import CAVEMAN_PROMPT


class _RecordingFactory(AgentFactory):
    """Captures which spec reached the (mocked) ADK build step."""

    def __init__(self, registry: SoulRegistry) -> None:
        super().__init__(registry, default_model="test-model")
        self.built: AgentSpec | None = None

    def _build_llm_agent(self, spec: AgentSpec, *, extra_tools: object = None) -> object:  # type: ignore[override]
        self.built = spec
        return object()


def _make_skill(skills_dir: Path, name: str, body: str) -> None:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test skill.\n---\n\n{body}\n"
    )


def test_new_spec_is_persisted(registry: SoulRegistry, sample_spec: AgentSpec) -> None:
    factory = _RecordingFactory(registry)

    factory.create_or_reuse(sample_spec)

    assert registry.get(sample_spec.key) == sample_spec
    assert factory.built == sample_spec


def test_existing_spec_is_reused(registry: SoulRegistry, sample_spec: AgentSpec) -> None:
    registry.save(sample_spec)
    factory = _RecordingFactory(registry)

    # Submit a spec with the same name but different instruction.
    incoming = sample_spec.model_copy(update={"instruction": "DIFFERENT"})
    factory.create_or_reuse(incoming)

    # The stored version wins — capability is reused, not rebuilt.
    assert factory.built == sample_spec
    assert factory.built is not None
    assert factory.built.instruction == sample_spec.instruction


def test_to_agent_card_shape(sample_spec: AgentSpec) -> None:
    card = to_agent_card(sample_spec, url="http://localhost:8000")

    assert card["name"] == "Email Summarizer"
    assert card["url"] == "http://localhost:8000"
    assert {s["id"] for s in card["skills"]} == {"summarization", "email"}


def test_skills_dir_injects_instruction(registry: SoulRegistry, tmp_path: Path) -> None:
    _make_skill(tmp_path, "caveman", "CAVEMAN RULES")
    spec = AgentSpec(
        name="Talker",
        description="Talks.",
        instruction="Base instruction.",
        model="test-model",
        skills=["caveman"],
    )
    # Subclass to capture the composed instruction without building a real LlmAgent.
    captured: dict[str, str] = {}

    class _Factory(AgentFactory):
        def _build_llm_agent(self, s: AgentSpec, *, extra_tools: object = None) -> object:  # type: ignore[override]
            from gaia.skills import attach_skills

            captured["instruction"] = attach_skills(s.instruction, s.skills, tmp_path)
            return object()

    _Factory(registry, default_model="test-model", skills_dir=tmp_path).create_or_reuse(spec)

    assert "Base instruction." in captured["instruction"]
    assert "CAVEMAN RULES" in captured["instruction"]


def test_to_agent_card_resolves_ids(tmp_path: Path) -> None:
    _make_skill(tmp_path, "caveman", "body")
    spec = AgentSpec(name="Talker", description="d", instruction="i", model="m", skills=["caveman"])

    card = to_agent_card(spec, skills_dir=tmp_path)

    skill = card["skills"][0]
    assert skill["name"] == "caveman"
    assert skill["description"] == "A test skill."


def _capture_instruction(
    factory: AgentFactory, spec: AgentSpec, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Build the agent with a recording LlmAgent to capture the composed instruction."""
    import google.adk.agents as adk

    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(adk, "LlmAgent", _Recorder)
    factory.create_or_reuse(spec)
    return str(captured["instruction"])


def test_factory_composes_default_style_and_skill(
    registry: SoulRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_skill(tmp_path, "caveman", "CAVEMAN RULES")
    spec = AgentSpec(
        name="Talker", description="d", instruction="Base.", model="m", skills=["caveman"]
    )
    factory = AgentFactory(
        registry, default_model="m", skills_dir=tmp_path, default_communication_style="caveman"
    )

    instruction = _capture_instruction(factory, spec, monkeypatch)

    assert instruction.startswith(CAVEMAN_PROMPT)  # style prepended as intro
    assert "Base." in instruction
    assert "CAVEMAN RULES" in instruction  # folder skill appended


def _capture_kwargs(
    factory: AgentFactory, spec: AgentSpec, monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    """Build the agent with a recording LlmAgent and return all kwargs it received."""
    import google.adk.agents as adk

    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(adk, "LlmAgent", _Recorder)
    factory.create_or_reuse(spec)
    return captured


def test_factory_resolves_and_passes_tools(
    registry: SoulRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaia.tools import ToolRegistry

    def _search() -> str:
        return "x"

    tool_registry = ToolRegistry()
    tool_registry.register("web_search", _search)
    spec = AgentSpec(
        name="Searcher", description="d", instruction="i", model="m", tools=["web_search"]
    )
    factory = AgentFactory(registry, default_model="m", tool_registry=tool_registry)

    kwargs = _capture_kwargs(factory, spec, monkeypatch)

    assert kwargs["tools"] == [_search]


def test_factory_appends_mcp_toolsets(
    registry: SoulRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = object()  # stands in for an McpToolset
    spec = AgentSpec(name="Soul", description="d", instruction="i", model="m")
    factory = AgentFactory(registry, default_model="m", mcp_toolsets_provider=lambda: [sentinel])

    kwargs = _capture_kwargs(factory, spec, monkeypatch)

    assert sentinel in kwargs["tools"]  # type: ignore[operator]  # souls get the MCP toolsets too


def test_factory_appends_skill_toolset(
    registry: SoulRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = object()  # stands in for the on-demand SkillToolset
    spec = AgentSpec(name="Soul", description="d", instruction="i", model="m")
    factory = AgentFactory(registry, default_model="m", skill_toolset_provider=lambda: [sentinel])

    kwargs = _capture_kwargs(factory, spec, monkeypatch)

    assert sentinel in kwargs["tools"]  # type: ignore[operator]  # souls reach the skills folder too


def test_factory_defaults_to_all_tools(
    registry: SoulRegistry, sample_spec: AgentSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaia.tools import ToolRegistry

    a, b = (lambda: "a"), (lambda: "b")
    tool_registry = ToolRegistry()
    tool_registry.register("a", a)
    tool_registry.register("b", b)
    factory = AgentFactory(registry, default_model="m", tool_registry=tool_registry)

    # sample_spec pins no tools, so the agent gets every registered tool.
    kwargs = _capture_kwargs(factory, sample_spec, monkeypatch)

    assert kwargs["tools"] == [a, b]


def test_factory_no_registry_passes_empty(
    registry: SoulRegistry, sample_spec: AgentSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = AgentFactory(registry, default_model="m")  # no tool_registry

    kwargs = _capture_kwargs(factory, sample_spec, monkeypatch)

    assert kwargs["tools"] == []


def test_spec_style_overrides_default(
    registry: SoulRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = AgentSpec(
        name="Talker",
        description="d",
        instruction="Base.",
        model="m",
        communication_style="ai",  # explicit raw voice, overrides default
    )
    factory = AgentFactory(registry, default_model="m", default_communication_style="caveman")

    instruction = _capture_instruction(factory, spec, monkeypatch)

    # 'ai' injects nothing, default 'caveman' ignored — only the soul preamble is prepended.
    from gaia.agents.factory import SOUL_PREAMBLE

    assert instruction == SOUL_PREAMBLE + "Base."

"""Factory reuse logic: a known spec is loaded from the registry, not recreated."""

from __future__ import annotations

from godpy.agents import AgentFactory, AgentRegistry, AgentSpec
from godpy.agents.factory import to_agent_card


class _RecordingFactory(AgentFactory):
    """Captures which spec reached the (mocked) ADK build step."""

    def __init__(self, registry: AgentRegistry) -> None:
        super().__init__(registry, default_model="test-model")
        self.built: AgentSpec | None = None

    def _build_llm_agent(self, spec: AgentSpec) -> object:  # type: ignore[override]
        self.built = spec
        return object()


def test_new_spec_is_persisted(registry: AgentRegistry, sample_spec: AgentSpec) -> None:
    factory = _RecordingFactory(registry)

    factory.create_or_reuse(sample_spec)

    assert registry.get(sample_spec.key) == sample_spec
    assert factory.built == sample_spec


def test_existing_spec_is_reused(registry: AgentRegistry, sample_spec: AgentSpec) -> None:
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

"""Registry persists and reloads AgentSpecs so agents are reused, not recreated."""

from __future__ import annotations

from godpy.agents import AgentRegistry, AgentSpec


def test_get_missing_returns_none(registry: AgentRegistry) -> None:
    assert registry.get("does-not-exist") is None


def test_save_then_get_roundtrips(registry: AgentRegistry, sample_spec: AgentSpec) -> None:
    registry.save(sample_spec)

    loaded = registry.get(sample_spec.key)

    assert loaded is not None
    assert loaded == sample_spec


def test_list_keys_returns_saved(registry: AgentRegistry, sample_spec: AgentSpec) -> None:
    registry.save(sample_spec)

    assert registry.list_keys() == [sample_spec.key]


def test_key_is_slugified(sample_spec: AgentSpec) -> None:
    assert sample_spec.key == "email_summarizer"

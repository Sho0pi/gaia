"""Registry persists and reloads AgentSpecs so agents are reused, not recreated."""

from __future__ import annotations

from gaia.agents import AgentSpec, SoulRegistry


def test_get_missing_returns_none(registry: SoulRegistry) -> None:
    assert registry.get("does-not-exist") is None


def test_save_then_get_roundtrips(registry: SoulRegistry, sample_spec: AgentSpec) -> None:
    registry.save(sample_spec)

    loaded = registry.get(sample_spec.key)

    assert loaded is not None
    assert loaded == sample_spec


def test_list_keys_returns_saved(registry: SoulRegistry, sample_spec: AgentSpec) -> None:
    registry.save(sample_spec)

    assert registry.list_keys() == [sample_spec.key]


def test_key_is_slugified(sample_spec: AgentSpec) -> None:
    assert sample_spec.key == "email_summarizer"


def test_delete_removes_and_reports(registry: SoulRegistry, sample_spec: AgentSpec) -> None:
    registry.save(sample_spec)

    assert registry.delete(sample_spec.key) is True
    assert registry.get(sample_spec.key) is None
    assert registry.delete(sample_spec.key) is False  # nothing left to delete


def test_saved_as_markdown(registry: SoulRegistry, sample_spec: AgentSpec, tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry.save(sample_spec)

    path = tmp_path / "agent_registry" / f"{sample_spec.key}.md"
    text = path.read_text()
    assert text.startswith("---\n")
    assert "name: Email Summarizer" in text
    assert sample_spec.instruction in text  # the long prose is the body, not frontmatter


def test_markdown_roundtrips(sample_spec: AgentSpec) -> None:
    assert AgentSpec.from_markdown(sample_spec.to_markdown()) == sample_spec


def test_from_markdown_rejects_missing_frontmatter() -> None:
    import pytest

    with pytest.raises(ValueError, match="frontmatter"):
        AgentSpec.from_markdown("just a body, no fences")

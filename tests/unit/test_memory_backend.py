"""build_mem0_config maps god.yaml's memory blocks to mem0's provider shape.

Pure dict assembly — no mem0 import, no network — so the provider-agnostic wiring
(defaults, overrides, secret handling) is checked offline.
"""

from __future__ import annotations

from pathlib import Path

from godpy.config import MemoryConfig, MemoryProvider, Settings
from godpy.memory.backend import DEFAULT_GEMINI_EMBEDDER_MODEL, build_mem0_config


def _settings() -> Settings:
    return Settings(model="gemini-2.0-flash", google_api_key="gem-key")  # type: ignore[call-arg]


def test_defaults_wire_gemini_and_chroma() -> None:
    cfg = build_mem0_config(_settings(), MemoryConfig())

    assert cfg["llm"] == {
        "provider": "gemini",
        "config": {"model": "gemini-2.0-flash", "api_key": "gem-key"},
    }
    assert cfg["embedder"]["provider"] == "gemini"
    assert cfg["embedder"]["config"]["model"] == DEFAULT_GEMINI_EMBEDDER_MODEL
    assert cfg["embedder"]["config"]["api_key"] == "gem-key"
    store = cfg["vector_store"]
    assert store["provider"] == "chroma"
    assert store["config"]["collection_name"] == "godpy"
    assert store["config"]["path"].endswith("memory/chroma")


def test_non_gemini_llm_carries_no_injected_key() -> None:
    # OpenAI/Anthropic/etc read their own env var inside mem0 — godpy never injects it.
    memory = MemoryConfig(
        llm=MemoryProvider(provider="openai", model="gpt-4o-mini")  # type: ignore[call-arg]
    )
    cfg = build_mem0_config(_settings(), memory)

    assert cfg["llm"]["provider"] == "openai"
    assert cfg["llm"]["config"] == {"model": "gpt-4o-mini"}  # no api_key, no gemini model


def test_user_extras_override_gemini_defaults() -> None:
    memory = MemoryConfig(
        llm=MemoryProvider(provider="gemini", model="gemini-2.5-pro")  # type: ignore[call-arg]
    )
    cfg = build_mem0_config(_settings(), memory)

    assert cfg["llm"]["config"]["model"] == "gemini-2.5-pro"  # override wins
    assert cfg["llm"]["config"]["api_key"] == "gem-key"  # default still filled


def test_pgvector_store_passes_extras_through() -> None:
    memory = MemoryConfig(
        vector_store=MemoryProvider(  # type: ignore[call-arg]
            provider="pgvector", host="db", port=5432
        )
    )
    cfg = build_mem0_config(_settings(), memory)

    store = cfg["vector_store"]
    assert store["provider"] == "pgvector"
    assert store["config"]["host"] == "db" and store["config"]["port"] == 5432
    assert store["config"]["collection_name"] == "godpy"  # default still applied
    assert "path" not in store["config"]  # path is chroma-only


def test_local_embedder_needs_no_key() -> None:
    memory = MemoryConfig(embedder=MemoryProvider(provider="fastembed"))
    cfg = build_mem0_config(_settings(), memory)

    assert cfg["embedder"] == {"provider": "fastembed", "config": {}}


def test_config_path_is_absolute() -> None:
    cfg = build_mem0_config(_settings(), MemoryConfig())
    assert Path(cfg["vector_store"]["config"]["path"]).is_absolute()

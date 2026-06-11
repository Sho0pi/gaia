"""Build a configured mem0 ``Memory`` client.

mem0 is orchestration, not a model: each ``add``/``search`` leans on an **LLM** (to
extract facts), an **embedder** (to vectorise) and a **vector store** (to hold them).
Each of the three is provider-agnostic — picked in ``gaia.yaml``'s ``memory`` section
(:class:`~gaia.config.schema.MemoryProvider`) and passed straight through to mem0.

The defaults wire **Gemini** for the LLM + embedder (reusing gaia's existing key and
model) and a local **chroma** vector store, so out of the box the heavy compute runs in
Google's cloud and the device only runs a small embedded store + mem0's SQLite history —
portable down to a Raspberry Pi. Point any component elsewhere (OpenAI, Anthropic via
litellm, a local embedder, pgvector/qdrant) without touching code.

**Secrets stay in env, never config.** No api key is ever injected here — every provider
reads its own standard env var inside mem0 (gemini reads ``GOOGLE_API_KEY``, which
``configure_adk_env`` already sets from ``GEMINI_API_KEY``; ``OPENAI_API_KEY`` etc. for
the rest), exactly like the agent model. So keys never land in the hand-edited ``gaia.yaml``.

The ``mem0`` import is deferred (heavy-deps convention) so this module imports cleanly
without a configured store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mem0 import Memory

    from gaia.config import MemoryConfig, MemoryProvider, Settings

#: Gemini embedding model used when the embedder provider is the (default) gemini one.
#: ``gemini-embedding-2`` is the current/GA model per ai.google.dev/gemini-api/docs/embeddings.
DEFAULT_GEMINI_EMBEDDER_MODEL = "models/gemini-embedding-2"


def _component(block: MemoryProvider, defaults: dict[str, Any]) -> dict[str, Any]:
    """Turn a :class:`MemoryProvider` into mem0's ``{provider, config}`` shape.

    Extra keys set in ``gaia.yaml`` win; ``defaults`` fill what the user left out (and
    only apply to the provider they belong to — the caller passes provider-specific
    defaults).
    """
    config = {**defaults, **(block.model_extra or {})}
    return {"provider": block.provider, "config": config}


def build_mem0_config(settings: Settings, memory: MemoryConfig) -> dict[str, Any]:
    """Assemble the mem0 ``from_config`` dict from gaia settings + ``gaia.yaml``.

    Each component falls back to a Gemini/chroma default; the Gemini defaults reuse
    ``settings.model`` and ``GEMINI_API_KEY`` so a stock install needs no extra config.
    """
    store_dir = constants.HOME_DIR / "memory" / "chroma"

    # Keys never come from config — every provider reads its own env var inside mem0, the
    # same way the agent model does (gemini reads GOOGLE_API_KEY, which configure_adk_env
    # sets). We only supply the model name for the gemini defaults.
    llm_defaults: dict[str, Any] = {}
    if memory.llm.provider == "gemini":
        llm_defaults = {"model": settings.model}

    embedder_defaults: dict[str, Any] = {}
    if memory.embedder.provider == "gemini":
        embedder_defaults = {"model": DEFAULT_GEMINI_EMBEDDER_MODEL}

    store_defaults: dict[str, Any] = {"collection_name": settings.mem0_collection}
    if memory.vector_store.provider == "chroma":
        store_defaults["path"] = str(store_dir)

    return {
        "llm": _component(memory.llm, llm_defaults),
        "embedder": _component(memory.embedder, embedder_defaults),
        "vector_store": _component(memory.vector_store, store_defaults),
    }


def build_mem0(settings: Settings, memory: MemoryConfig) -> Memory:
    """Construct a mem0 ``Memory`` configured from ``settings`` + ``memory`` config."""
    from mem0 import Memory

    return Memory.from_config(build_mem0_config(settings, memory))

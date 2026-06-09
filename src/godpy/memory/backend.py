"""Build a configured mem0 ``Memory`` client.

mem0 is orchestration, not a model: each ``add``/``search`` leans on an **LLM** (to
extract facts), an **embedder** (to vectorise) and a **vector store** (to hold them).
We point the first two at **Gemini** — reusing godpy's existing key/model, so the heavy
compute runs in Google's cloud and the device only runs a small embedded vector store
(chroma) plus mem0's SQLite history. That keeps godpy's memory portable: anything that
can already reach Gemini for the agent can also remember, down to a Raspberry Pi.

The ``mem0`` import is deferred (heavy-deps convention) so this module imports cleanly
without a configured store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from godpy import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mem0 import Memory

    from godpy.config import MemoryConfig
    from godpy.config.settings import Settings

#: Gemini embedding model used to vectorise memories.
DEFAULT_EMBEDDER_MODEL = "models/gemini-embedding-001"

#: Embedded, file-backed vector store — no server, runs anywhere Python does.
DEFAULT_VECTOR_STORE = "chroma"


def build_mem0_config(settings: Settings, memory: MemoryConfig) -> dict[str, Any]:
    """Assemble the mem0 ``from_config`` dict from godpy settings + ``god.yaml``.

    LLM and embedder are Gemini (reusing ``settings.model`` / ``GEMINI_API_KEY``); the
    vector store defaults to a local chroma dir under the home folder, keyed by
    ``settings.mem0_collection``. ``memory.vector_store`` overrides the provider for
    leaner/heavier hosts without touching code.
    """
    store_dir = constants.HOME_DIR / "memory" / "chroma"
    api_key = settings.google_api_key
    return {
        "llm": {
            "provider": "gemini",
            "config": {"model": settings.model, "api_key": api_key},
        },
        "embedder": {
            "provider": "gemini",
            "config": {"model": DEFAULT_EMBEDDER_MODEL, "api_key": api_key},
        },
        "vector_store": {
            "provider": memory.vector_store or DEFAULT_VECTOR_STORE,
            "config": {
                "collection_name": settings.mem0_collection,
                "path": str(store_dir),
            },
        },
    }


def build_mem0(settings: Settings, memory: MemoryConfig) -> Memory:
    """Construct a Gemini-backed mem0 ``Memory`` for ``settings`` + ``memory`` config."""
    from mem0 import Memory

    return Memory.from_config(build_mem0_config(settings, memory))

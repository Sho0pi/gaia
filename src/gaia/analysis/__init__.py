"""Mine the structured user-activity events into growth proposals (issue #19).

``events`` turns ``events.jsonl`` into a compact :class:`EventDigest` **in code** — the
LLM only ever sees the rendered digest, never raw log lines. ``analyst`` is the
smith-style decision agent that reads the digest and proposes new skills / memory
writes, each gated behind human approval in ``gaia analyze``.
"""

from gaia.analysis.analyst import (
    AnalysisReport,
    MemoryProposal,
    SkillProposal,
    SoulProposal,
    build_analyst,
)
from gaia.analysis.events import EventDigest, digest_events, read_events, render_digest

__all__ = [
    "AnalysisReport",
    "EventDigest",
    "MemoryProposal",
    "SkillProposal",
    "SoulProposal",
    "build_analyst",
    "digest_events",
    "read_events",
    "render_digest",
]

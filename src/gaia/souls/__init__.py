"""Souls — Gaia's specialist subagents, forged on demand and reused.

A *soul* is an :class:`~gaia.agents.spec.AgentSpec`. For a complex task Gaia calls
:func:`~gaia.souls.delegate.make_delegate`'s tool, which has the
:mod:`~gaia.souls.smith` reuse an existing soul or forge a new one, then runs it in its
own workspace. See issue #41.
"""

from gaia.souls.delegate import NAME, make_delegate
from gaia.souls.smith import SoulDecision, build_soul_smith

__all__ = ["NAME", "SoulDecision", "build_soul_smith", "make_delegate"]

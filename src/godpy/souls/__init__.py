"""Souls — God's specialist subagents, forged on demand and reused.

A *soul* is an :class:`~godpy.agents.spec.AgentSpec`. For a complex task God calls
:func:`~godpy.souls.delegate.make_delegate`'s tool, which has the
:mod:`~godpy.souls.smith` reuse an existing soul or forge a new one, then runs it in its
own workspace. See issue #41.
"""

from godpy.souls.delegate import NAME, make_delegate
from godpy.souls.smith import SoulDecision, build_soul_smith

__all__ = ["NAME", "SoulDecision", "build_soul_smith", "make_delegate"]

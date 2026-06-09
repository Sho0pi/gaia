"""Dynamic subagent factory + registry.

God builds an :class:`AgentSpec` for a task, the factory turns it into an ADK
agent, and the registry persists it as a reusable A2A AgentCard so the same
capability is never rebuilt twice.
"""

from godpy.agents.factory import AgentFactory
from godpy.agents.registry import SoulRegistry
from godpy.agents.spec import AgentSpec

__all__ = ["AgentFactory", "AgentSpec", "SoulRegistry"]

"""Dynamic subagent factory + registry.

Gaia builds an :class:`AgentSpec` for a task, the factory turns it into an ADK
agent, and the registry persists it as a reusable A2A AgentCard so the same
capability is never rebuilt twice.
"""

from gaia.agents.factory import AgentFactory
from gaia.agents.registry import SoulRegistry
from gaia.agents.spec import AgentSpec

__all__ = ["AgentFactory", "AgentSpec", "SoulRegistry"]

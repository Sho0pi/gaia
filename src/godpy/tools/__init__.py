"""Callable tools the LLM can invoke, and the in-memory registry that holds them.

A tool is a plain Python function with type hints + a docstring; ADK auto-generates
its schema. :class:`ToolRegistry` maps tool ids to those callables so the factory can
attach exactly the tools an :class:`~godpy.agents.spec.AgentSpec` asks for.

Individual tools live in their own modules (``web_search``, ``web_fetch``, the ``fs``
package); import those directly when you need a specific factory.
"""

from godpy.tools.registry import (
    Tool,
    ToolRegistry,
    default_registry,
)

__all__ = ["Tool", "ToolRegistry", "default_registry"]

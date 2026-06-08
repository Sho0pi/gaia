"""Callable tools the LLM can invoke, and the in-memory registry that holds them.

A tool is a plain Python function with type hints + a docstring; ADK auto-generates
its schema. :class:`ToolRegistry` maps tool ids to those callables so the factory can
attach exactly the tools an :class:`~godpy.agents.spec.AgentSpec` asks for.
"""

from godpy.tools.registry import Tool, ToolRegistry, default_registry
from godpy.tools.web_search import (
    SearchProvider,
    ddg_provider,
    make_web_search,
)

__all__ = [
    "SearchProvider",
    "Tool",
    "ToolRegistry",
    "ddg_provider",
    "default_registry",
    "make_web_search",
]

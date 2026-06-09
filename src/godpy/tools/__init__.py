"""Callable tools the LLM can invoke, and the in-memory registry that holds them.

A tool is a plain Python function with type hints + a docstring; ADK auto-generates
its schema. :class:`ToolRegistry` maps tool ids to those callables so the factory can
attach exactly the tools an :class:`~godpy.agents.spec.AgentSpec` asks for.
"""

from godpy.tools.fs import (
    Sandbox,
    SandboxError,
    make_fs_edit,
    make_fs_glob,
    make_fs_grep,
    make_fs_read,
    make_fs_write,
)
from godpy.tools.registry import Tool, ToolRegistry, default_registry
from godpy.tools.web_fetch import (
    Fetcher,
    httpx_fetcher,
    make_web_fetch,
    validate_url,
)
from godpy.tools.web_search import (
    SearchProvider,
    ddg_provider,
    make_web_search,
)

__all__ = [
    "Fetcher",
    "Sandbox",
    "SandboxError",
    "SearchProvider",
    "Tool",
    "ToolRegistry",
    "ddg_provider",
    "default_registry",
    "httpx_fetcher",
    "make_fs_edit",
    "make_fs_glob",
    "make_fs_grep",
    "make_fs_read",
    "make_fs_write",
    "make_web_fetch",
    "make_web_search",
    "validate_url",
]

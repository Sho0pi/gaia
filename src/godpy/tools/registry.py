"""In-memory registry of callable tools the LLM can invoke.

Unlike :class:`godpy.agents.registry.AgentRegistry`, tools are *code*, not data, so
there is nothing to persist as JSON — the registry is a plain name → callable map,
populated once at startup by :func:`default_registry`. A tool is a plain Python
function with type hints + a docstring; ADK turns it into a tool schema on its own
(no manual schema), so the registry only has to hand the right callables to the
factory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from godpy.tools.web_search import ddg_provider, make_web_search

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.config import GodConfig

# An ADK tool is just a callable; ADK derives name/description/schema from it.
Tool = Callable[..., Any]


class ToolRegistry:
    """Name → tool map. The unit the factory resolves an ``AgentSpec.tools`` against."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, fn: Tool) -> None:
        """Add ``fn`` under ``name``; a later registration replaces an earlier one."""
        self._tools[name] = fn

    def get(self, name: str) -> Tool:
        """Return the tool registered as ``name`` or raise with the known names."""
        try:
            return self._tools[name]
        except KeyError:
            known = ", ".join(self.names()) or "<none>"
            raise KeyError(f"unknown tool {name!r}; registered: {known}") from None

    def resolve(self, ids: Iterable[str]) -> list[Any]:
        """Map each id to its callable (order preserved), raising on any unknown id.

        Returns ``list[Any]`` so the result drops straight into ADK's invariant
        ``LlmAgent(tools=...)`` (a ``Callable | BaseTool | BaseToolset`` list).
        """
        return [self.get(name) for name in ids]

    def all(self) -> list[Tool]:
        """Every registered tool, in name order."""
        return [self._tools[name] for name in self.names()]

    def names(self) -> list[str]:
        """Every registered tool id, sorted."""
        return sorted(self._tools)


def _is_enabled(config: GodConfig | None, name: str) -> bool:
    """A tool is on unless ``god.yaml`` lists it with ``enabled: false``."""
    if config is None:
        return True
    entry = config.tools.get(name)
    return True if entry is None else entry.enabled


def default_registry(config: GodConfig | None = None) -> ToolRegistry:
    """Build the registry with godpy's built-in tools, honoring ``config.tools`` flags.

    The provider for each tool is injected here, so swapping ``web_search`` from
    DuckDuckGo to another backend later is a one-line change with no effect on the
    tool's call site.
    """
    registry = ToolRegistry()
    if _is_enabled(config, "web_search"):
        registry.register("web_search", make_web_search(ddg_provider))
    return registry

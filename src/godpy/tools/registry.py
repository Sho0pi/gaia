"""In-memory registry of callable tools the LLM can invoke.

Unlike :class:`godpy.agents.registry.SoulRegistry`, tools are *code*, not data, so
there is nothing to persist as JSON — the registry is a plain name → callable map,
populated once at startup by :func:`default_registry`. A tool is a plain Python
function with type hints + a docstring; ADK turns it into a tool schema on its own
(no manual schema), so the registry only has to hand the right callables to the
factory.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Union

from godpy import constants
from godpy.tools import fs, shell
from godpy.tools.remember import NAME as REMEMBER
from godpy.tools.remember import make_remember
from godpy.tools.web_fetch import NAME as WEB_FETCH
from godpy.tools.web_fetch import httpx_fetcher, make_web_fetch
from godpy.tools.web_search import NAME as WEB_SEARCH
from godpy.tools.web_search import get_search_provider, make_web_search

#: ADK's built-in memory-fetch tool id (registered as the agent-facing tool name).
LOAD_MEMORY = "load_memory"

#: godpy's own tools each emit their own ``tool_used`` event (rich, tool-specific fields)
#: from their ``done()`` closure. The central ToolLoggingPlugin skips these to avoid
#: double-logging and covers everything else (ADK built-ins like ``load_memory``).
SELF_LOGGING_TOOLS = frozenset(
    {
        WEB_FETCH,
        WEB_SEARCH,
        REMEMBER,
        fs.READ,
        fs.WRITE,
        fs.EDIT,
        fs.GLOB,
        fs.GREP,
        shell.EXEC,
        shell.POLL,
        shell.KILL,
        shell.LIST,
    }
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.base_toolset import BaseToolset

    from godpy.config import GodConfig

# Exactly what ADK's ``LlmAgent(tools=...)`` accepts: a plain callable (ADK derives
# name/description/schema from it) or an ADK tool/toolset object. Typed against the
# framework so the registry can also hold built-in BaseTools, and resolved lists drop
# straight into the (invariant) ADK tools list. ADK is imported only under
# TYPE_CHECKING to keep this module importable without a model backend.
Tool = Union[Callable[..., Any], "BaseTool", "BaseToolset"]


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

    def resolve(self, ids: Iterable[str]) -> list[Tool]:
        """Map each id to its tool (order preserved), raising on any unknown id."""
        return [self.get(name) for name in ids]

    def all(self) -> list[Tool]:
        """Every registered tool, in name order."""
        return [self._tools[name] for name in self.names()]

    def names(self) -> list[str]:
        """Every registered tool id, sorted."""
        return sorted(self._tools)


def _tool_setting(config: GodConfig | None, name: str, key: str) -> Any | None:
    """Read a per-tool config value (``tools.<name>.<key>``), or None if unset."""
    if config is None:
        return None
    entry = config.tools.get(name)
    if entry is None:
        return None
    return (entry.model_extra or {}).get(key)


def _is_enabled(config: GodConfig | None, name: str) -> bool:
    """A tool is on unless ``god.yaml`` lists it with ``enabled: false``."""
    if config is None:
        return True
    entry = config.tools.get(name)
    return True if entry is None else entry.enabled


def _register_shell_tools(registry: ToolRegistry, config: GodConfig | None) -> None:
    """Attach the exec tool + its background-process trio, sharing one ProcessManager.

    Safety comes from ``tools.exec.security`` (default ``allowlist``) and an optional
    ``tools.exec.allowlist`` override, both read from config. The trio (poll/kill/list)
    is only useful alongside ``exec``, but each stays individually gateable.
    """
    security = _tool_setting(config, shell.EXEC, "security") or "allowlist"
    configured = _tool_setting(config, shell.EXEC, "allowlist")
    allowlist = tuple(configured) if configured else shell.DEFAULT_ALLOWLIST

    manager = shell.default_process_manager()
    spawner = shell.local_spawner
    if _is_enabled(config, shell.EXEC):
        registry.register(
            shell.EXEC,
            shell.make_exec(manager, spawner, security=security, allowlist=allowlist),
        )
    if _is_enabled(config, shell.POLL):
        registry.register(shell.POLL, shell.make_exec_poll(manager))
    if _is_enabled(config, shell.KILL):
        registry.register(shell.KILL, shell.make_exec_kill(manager))
    if _is_enabled(config, shell.LIST):
        registry.register(shell.LIST, shell.make_exec_list(manager))


def default_registry(config: GodConfig | None = None) -> ToolRegistry:
    """Build the registry with all of godpy's built-in tools, configured from ``config``.

    Each tool is on by default and gated only by its ``enabled`` flag (and, where it needs
    one, an external resource such as a configured engine or a binary on ``PATH``).
    """
    registry = ToolRegistry()

    if _is_enabled(config, WEB_FETCH):
        registry.register(WEB_FETCH, make_web_fetch(httpx_fetcher))

    engine = _tool_setting(config, WEB_SEARCH, "engine")
    if engine and _is_enabled(config, WEB_SEARCH):
        registry.register(WEB_SEARCH, make_web_search(get_search_provider(engine)))

    agents_dir = constants.AGENTS_DIR
    if _is_enabled(config, fs.READ):
        registry.register(fs.READ, fs.make_fs_read(agents_dir))
    if _is_enabled(config, fs.WRITE):
        registry.register(fs.WRITE, fs.make_fs_write(agents_dir))
    if _is_enabled(config, fs.EDIT):
        registry.register(fs.EDIT, fs.make_fs_edit(agents_dir))
    if _is_enabled(config, fs.GLOB) and shutil.which("fd"):
        registry.register(fs.GLOB, fs.make_fs_glob(agents_dir))
    if _is_enabled(config, fs.GREP) and shutil.which("rg"):
        registry.register(fs.GREP, fs.make_fs_grep(agents_dir))

    _register_shell_tools(registry, config)

    # Memory tools are only useful when long-term memory is on (mem0 wired into the
    # Runner); each is still individually gateable via tools.<name>.enabled.
    if config is None or config.memory.enabled:
        if _is_enabled(config, LOAD_MEMORY):
            from google.adk.tools.load_memory_tool import load_memory_tool

            registry.register(LOAD_MEMORY, load_memory_tool)
        if _is_enabled(config, REMEMBER):
            registry.register(REMEMBER, make_remember())

    return registry

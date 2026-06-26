"""In-memory registry of callable tools the LLM can invoke.

Unlike :class:`gaia.agents.registry.SoulRegistry`, tools are *code*, not data, so
there is nothing to persist as JSON — the registry is a plain name → callable map,
populated once at startup by :func:`default_registry`. A tool is a plain Python
function with type hints + a docstring; ADK turns it into a tool schema on its own
(no manual schema), so the registry only has to hand the right callables to the
factory.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any, Union

from gaia import constants
from gaia.tools import browser, capabilities, fs, image, shell
from gaia.tools.ask_user import NAME as ASK_USER
from gaia.tools.ask_user import make_ask_user
from gaia.tools.cron import NAME as CRON
from gaia.tools.cron import make_cron
from gaia.tools.remember import NAME as REMEMBER
from gaia.tools.remember import make_remember
from gaia.tools.set_communication_style import NAME as SET_STYLE
from gaia.tools.set_communication_style import make_set_communication_style
from gaia.tools.task import (
    TASK_COMPLETE,
    TASK_CREATE,
    TASK_GET,
    TASK_LIST,
    TASK_UPDATE,
    make_task_complete,
    make_task_create,
    make_task_get,
    make_task_list,
    make_task_update,
)
from gaia.tools.web_fetch import NAME as WEB_FETCH
from gaia.tools.web_fetch import httpx_fetcher, make_web_fetch
from gaia.tools.web_search import NAME as WEB_SEARCH
from gaia.tools.web_search import get_search_provider, make_web_search

logger = logging.getLogger(__name__)

#: ADK's built-in memory-fetch tool id (registered as the agent-facing tool name).
LOAD_MEMORY = "load_memory"

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.base_toolset import BaseToolset

    from gaia.config import GaiaConfig
    from gaia.missions import TaskStore

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
        self._closeables: list[Callable[[], Awaitable[None]]] = []
        #: Tools that were *wanted* (enabled) but couldn't be registered, mapped to a
        #: human reason + remedy. Surfaced at startup (warning) and in ``/status``.
        self.missing: dict[str, str] = {}

    def register(self, name: str, fn: Tool) -> None:
        """Add ``fn`` under ``name``; a later registration replaces an earlier one."""
        self._tools[name] = fn

    def mark_missing(self, name: str, reason: str) -> None:
        """Record (and warn once) that enabled tool ``name`` was skipped — with the remedy."""
        self.missing[name] = reason
        logger.warning("%s disabled: %s", name, reason)

    def register_closeable(self, close: Callable[[], Awaitable[None]]) -> None:
        """Record an async cleanup (e.g. a tool manager's ``close_all``) for :meth:`aclose`.

        Lets stateful tool backends (the shell ProcessManager, the browser session
        manager) be torn down by :meth:`Gaia.close` on the *running* loop that owns their
        subprocesses/connections — instead of falling through to their ``atexit`` hook,
        which runs after that loop is gone and raises 'Event loop is closed'.
        """
        self._closeables.append(close)

    async def aclose(self) -> None:
        """Run every registered cleanup, best-effort (one failure never blocks the rest)."""
        for close in self._closeables:
            try:
                await close()
            except Exception:  # pragma: no cover - shutdown best-effort
                logger.debug("tool cleanup failed", exc_info=True)

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


def _tool_setting(config: GaiaConfig | None, name: str, key: str) -> Any | None:
    """Read a per-tool config value (``tools.<name>.<key>``), or None if unset."""
    if config is None:
        return None
    entry = config.tools.get(name)
    if entry is None:
        return None
    return (entry.model_extra or {}).get(key)


def _is_enabled(config: GaiaConfig | None, name: str) -> bool:
    """A tool is on unless ``gaia.yaml`` lists it with ``enabled: false``."""
    if config is None:
        return True
    entry = config.tools.get(name)
    return True if entry is None else entry.enabled


#: The browser tools and their factory builders. They share one session manager so
#: every tool acts on the same per-agent page.
_BROWSER_TOOLS = (
    (browser.NAVIGATE, browser.make_browser_navigate),
    (browser.SNAPSHOT, browser.make_browser_snapshot),
    (browser.CLICK, browser.make_browser_click),
    (browser.TYPE, browser.make_browser_type),
    (browser.SCREENSHOT, browser.make_browser_screenshot),
)


def _register_browser_tools(
    registry: ToolRegistry, config: GaiaConfig | None, served: Any = None
) -> None:
    """Attach the browser tools, but only when Playwright is installed.

    Like fs_glob/fs_grep need ``fd``/``rg``, the browser tools need the optional
    ``browser`` dependency group. When it's absent we skip them and warn (rather than
    crash), naming the remedy — so a soul's instruction that references the browser
    just degrades instead of taking the whole app down.
    """
    enabled = [name for name, _ in _BROWSER_TOOLS if _is_enabled(config, name)]
    if not enabled:
        return
    # When the mcp backend is effective (playwright-mcp via bunx), the browser is provided
    # by Gaia.mcp_toolsets — don't also register the native tools. The resolver falls back
    # to "native" when the runtime is missing, so that case still registers them here.
    from gaia.config.schema import BrowserConfig
    from gaia.mcp import resolve_browser_backend

    browser_cfg = config.browser if config is not None else BrowserConfig()
    if resolve_browser_backend(browser_cfg) == "mcp":
        return
    if importlib.util.find_spec("playwright") is None:
        logger.warning(
            "browser tools disabled: Playwright not installed (run 'uv sync --group "
            "browser && uv run playwright install chromium')"
        )
        return
    # One manager per registry, shared by the browser tools (each closure captures it);
    # it closes its sessions on exit. No module-level singleton.
    manager = browser.BrowserSessionManager()
    registry.register_closeable(manager.close_all)  # closed by Gaia.close on the live loop
    for name, make in _BROWSER_TOOLS:
        if not _is_enabled(config, name):
            continue
        if name == browser.NAVIGATE:
            # navigate also needs the served-ports set so it can open our own serve-d sites
            # (loopback) past the SSRF guard; the other browser tools take only the manager.
            registry.register(name, browser.make_browser_navigate(manager, served))
        else:
            registry.register(name, make(manager))


def _register_shell_tools(registry: ToolRegistry, config: GaiaConfig | None, served: Any) -> None:
    """Attach the exec tool + its background-process trio, sharing one ProcessManager.

    Safety comes from ``tools.exec.security`` (default ``allowlist``) and an optional
    ``tools.exec.allowlist`` override, both read from config. The trio (poll/kill/list)
    is only useful alongside ``exec``, but each stays individually gateable. ``served`` (the
    shared ServedPorts) lets a background dev server's port be opened by browser_navigate.
    """
    security = _tool_setting(config, shell.EXEC, "security") or "allowlist"
    configured = _tool_setting(config, shell.EXEC, "allowlist")
    allowlist = tuple(configured) if configured else shell.DEFAULT_ALLOWLIST

    # One manager per registry, shared by the four tools below (each closure captures
    # it); it cleans up its processes on exit. No module-level singleton.
    manager = shell.ProcessManager(served=served)
    registry.register_closeable(manager.close_all)  # closed by Gaia.close on the live loop
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
    # capabilities surfaces the SAME live security/allowlist exec was built with (+ the fs/serve
    # workspace rules) so the model can check what it can run instead of erroring into the sandbox.
    if _is_enabled(config, capabilities.NAME):
        registry.register(capabilities.NAME, capabilities.make_capabilities(security, allowlist))


def _register_serve_tools(registry: ToolRegistry, config: GaiaConfig | None, served: Any) -> None:
    """Attach the serve/serve_stop/serve_list tools, sharing one StaticServerManager.

    ``served`` is the ports set shared with browser_navigate (so it can open these sites).
    Idle window is ``tools.serve.idle_seconds``; public tunneling is configured under
    ``tools.serve.tunnel`` (off by default).
    """
    from gaia.tools import serve

    if not any(
        _is_enabled(config, name) for name in (serve.SERVE, serve.SERVE_STOP, serve.SERVE_LIST)
    ):
        return
    idle = _tool_setting(config, serve.SERVE, "idle_seconds")
    manager = serve.StaticServerManager(
        served, idle_seconds=float(idle) if idle else serve.DEFAULT_IDLE_SECONDS
    )
    registry.register_closeable(manager.close_all)  # closed by Gaia.close on the live loop

    # Public tunneling: read tools.serve.tunnel.{enabled,provider,runtime,timeout_seconds}.
    tunnel_cfg = _tool_setting(config, serve.SERVE, "tunnel") or {}
    tunnel_enabled = bool(tunnel_cfg.get("enabled", False))
    tunnel = serve.TunnelManager(
        provider=str(tunnel_cfg.get("provider", "pinggy")),
        runtime=str(tunnel_cfg.get("runtime", "bunx")),
        timeout_seconds=float(tunnel_cfg.get("timeout_seconds", serve.DEFAULT_TIMEOUT_SECONDS)),
    )
    registry.register_closeable(tunnel.close_all)

    if _is_enabled(config, serve.SERVE):
        registry.register(
            serve.SERVE, serve.make_serve(manager, tunnel, tunnel_enabled=tunnel_enabled)
        )
    if _is_enabled(config, serve.SERVE_STOP):
        registry.register(serve.SERVE_STOP, serve.make_serve_stop(manager, tunnel))
    if _is_enabled(config, serve.SERVE_LIST):
        registry.register(serve.SERVE_LIST, serve.make_serve_list(manager, tunnel))


def _register_task_tools(
    registry: ToolRegistry, config: GaiaConfig | None, store: TaskStore | None = None
) -> None:
    """Register the five missions task_* tools (one shared store), each gated by its flag."""
    from gaia.missions import TaskStore

    factories = (
        (TASK_CREATE, make_task_create),
        (TASK_LIST, make_task_list),
        (TASK_GET, make_task_get),
        (TASK_UPDATE, make_task_update),
        (TASK_COMPLETE, make_task_complete),
    )
    if not any(_is_enabled(config, name) for name, _ in factories):
        return
    store = store or TaskStore()  # the DI-shared board; falls back for direct callers/tests
    missions = config.missions if config is not None else None
    max_depth = missions.max_depth if missions is not None else 3
    max_tasks = missions.max_tasks if missions is not None else 20
    for name, make in factories:
        if not _is_enabled(config, name):
            continue
        if name == TASK_CREATE:
            registry.register(
                name, make_task_create(store, max_depth=max_depth, max_tasks=max_tasks)
            )
        else:
            registry.register(name, make(store))


def default_registry(
    config: GaiaConfig | None = None, task_store: TaskStore | None = None
) -> ToolRegistry:
    """Build the registry with all of gaia's built-in tools, configured from ``config``.

    Each tool is on by default and gated only by its ``enabled`` flag (and, where it needs
    one, an external resource such as a configured engine or a binary on ``PATH``).
    ``task_store`` is the DI-shared missions board the ``task_*`` tools bind to (the
    container passes it so the tools, ``task_plan`` and the dispatcher share one store).
    """
    registry = ToolRegistry()

    if _is_enabled(config, WEB_FETCH):
        registry.register(WEB_FETCH, make_web_fetch(httpx_fetcher))

    if _is_enabled(config, CRON):
        registry.register(CRON, make_cron())

    if _is_enabled(config, SET_STYLE):
        registry.register(SET_STYLE, make_set_communication_style())

    if _is_enabled(config, image.NAME):
        provider = _tool_setting(config, image.NAME, "provider") or "gemini"
        model = _tool_setting(config, image.NAME, "model") or ""
        options: dict[str, Any] | None = None
        if provider == "cloudflare":
            # The Worker url (non-secret) + optional SDXL knobs live in gaia.yaml; the token is an
            # env secret read by the backend. Only keys the user set are forwarded.
            options = {"url": _tool_setting(config, image.NAME, "cloudflare_url")}
            for key in ("num_steps", "guidance", "width", "height", "seed"):
                value = _tool_setting(config, image.NAME, key)
                if value is not None:
                    options[key] = value
        registry.register(
            image.NAME, image.make_generate_image(str(provider), str(model), options=options)
        )

    # ask_user pauses the run to ask the human (a choice or a missing credential) and
    # resumes on their reply; the handler surfaces the question and routes the answer.
    if _is_enabled(config, ASK_USER):
        registry.register(ASK_USER, make_ask_user())

    # Missions task board (P1): the five task_* tools share one TaskStore so they all hit
    # the same ~/.gaia/tasks.db. Gaia-only for now; souls get them in P3.
    _register_task_tools(registry, config, task_store)

    if _is_enabled(config, WEB_SEARCH):
        engine = _tool_setting(config, WEB_SEARCH, "engine")
        if not engine:
            registry.mark_missing(
                WEB_SEARCH, "set tools.web_search.engine in gaia.yaml (e.g. duckduckgo, brave)"
            )
        else:
            try:
                provider = get_search_provider(engine)  # may need an API key (e.g. brave)
            except ValueError as exc:
                registry.mark_missing(WEB_SEARCH, str(exc))
            else:
                registry.register(WEB_SEARCH, make_web_search(provider))

    agents_dir = constants.AGENTS_DIR
    if _is_enabled(config, fs.READ):
        registry.register(fs.READ, fs.make_fs_read(agents_dir))
    if _is_enabled(config, fs.WRITE):
        registry.register(fs.WRITE, fs.make_fs_write(agents_dir))
    if _is_enabled(config, fs.EDIT):
        registry.register(fs.EDIT, fs.make_fs_edit(agents_dir))
    if _is_enabled(config, fs.GLOB):
        fd_bin = shutil.which("fd") or shutil.which("fdfind")  # Debian renames fd -> fdfind
        if fd_bin:
            registry.register(fs.GLOB, fs.make_fs_glob(agents_dir, fd_bin))
        else:
            registry.mark_missing(
                fs.GLOB, "'fd' not on PATH (brew install fd / apt install fd-find)"
            )
    if _is_enabled(config, fs.GREP):
        if shutil.which("rg"):
            registry.register(fs.GREP, fs.make_fs_grep(agents_dir))
        else:
            registry.mark_missing(
                fs.GREP, "'rg' not on PATH (brew install ripgrep / apt install ripgrep)"
            )

    # The serve tools and browser_navigate share one ServedPorts set: serve writes the
    # ports it opens, navigate reads them to allow our own loopback sites past the SSRF
    # guard. Build it first so both registrations get the same instance.
    from gaia.tools.serve import ServedPorts

    served = ServedPorts()
    _register_serve_tools(registry, config, served)
    _register_browser_tools(registry, config, served)
    _register_shell_tools(registry, config, served)

    # Memory tools are only useful when long-term memory is on (mem0 wired into the
    # Runner); each is still individually gateable via tools.<name>.enabled.
    if config is None or config.memory.enabled:
        if _is_enabled(config, LOAD_MEMORY):
            from google.adk.tools.load_memory_tool import load_memory_tool

            registry.register(LOAD_MEMORY, load_memory_tool)
        if _is_enabled(config, REMEMBER):
            registry.register(REMEMBER, make_remember())

    return registry

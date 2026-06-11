"""ToolRegistry resolution + default_registry enabled-flag filtering."""

from __future__ import annotations

import pytest

from gaia.config import BrowserConfig, GaiaConfig, MemoryConfig, ToolConfig
from gaia.tools import ToolRegistry, default_registry


def _noop() -> str:
    return "ok"


def test_register_get_resolve_order() -> None:
    registry = ToolRegistry()
    registry.register("b", _noop)
    registry.register("a", _noop)

    assert registry.get("a") is _noop
    assert registry.names() == ["a", "b"]  # sorted
    assert registry.resolve(["b", "a"]) == [_noop, _noop]  # input order preserved


def test_get_unknown_raises_with_known_names() -> None:
    registry = ToolRegistry()
    registry.register("web_search", _noop)

    with pytest.raises(KeyError, match="unknown tool 'nope'; registered: web_search"):
        registry.get("nope")


def test_resolve_unknown_raises() -> None:
    with pytest.raises(KeyError):
        ToolRegistry().resolve(["missing"])


def test_register_replaces_earlier() -> None:
    registry = ToolRegistry()
    registry.register("t", _noop)
    other = lambda: "other"  # noqa: E731
    registry.register("t", other)

    assert registry.get("t") is other


def test_web_fetch_installed_by_default() -> None:
    # On with no config at all, and when present without enabled: false.
    assert "web_fetch" in default_registry().names()
    config = GaiaConfig(tools={"web_fetch": ToolConfig()})
    assert "web_fetch" in default_registry(config).names()


def test_web_fetch_removed_when_disabled() -> None:
    config = GaiaConfig(tools={"web_fetch": ToolConfig(enabled=False)})

    assert "web_fetch" not in default_registry(config).names()


def test_fs_tools_on_by_default() -> None:
    names = default_registry().names()

    assert "fs_read" in names
    assert "fs_write" in names
    assert "fs_edit" in names


def test_fs_tool_removed_when_disabled() -> None:
    config = GaiaConfig(tools={"fs_write": ToolConfig(enabled=False)})

    assert "fs_write" not in default_registry(config).names()


def test_fs_glob_grep_absent_without_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    import gaia.tools.registry as registry

    monkeypatch.setattr(registry.shutil, "which", lambda _name: None)
    names = default_registry().names()

    assert "fs_glob" not in names
    assert "fs_grep" not in names
    assert "fs_read" in names  # pure-python fs tools unaffected


def test_native_browser_registered_when_backend_native() -> None:
    pytest.importorskip("playwright", reason="native browser tools need the 'browser' group")
    # Backend is explicitly native, so the native tools register regardless of bunx.
    config = GaiaConfig(browser=BrowserConfig(backend="native"))

    assert "browser_navigate" in default_registry(config).names()


def test_native_browser_skipped_when_backend_mcp_and_runtime_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # resolve_browser_backend reads gaia.mcp.shutil.which — pretend bunx is on PATH.
    monkeypatch.setattr("gaia.mcp.shutil.which", lambda cmd: "/usr/bin/bunx")
    config = GaiaConfig(browser=BrowserConfig(backend="mcp"))  # the default

    # Provided by playwright-mcp (Gaia.mcp_toolsets), not the native registry.
    assert "browser_navigate" not in default_registry(config).names()


def test_native_browser_used_when_mcp_requested_but_runtime_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright", reason="native browser tools need the 'browser' group")
    monkeypatch.setattr("gaia.mcp.shutil.which", lambda cmd: None)  # no bunx → fall back
    config = GaiaConfig(browser=BrowserConfig(backend="mcp"))

    assert "browser_navigate" in default_registry(config).names()  # graceful native fallback


def test_web_search_not_installed_without_engine() -> None:
    # No config at all: engine is unconfigured, so the tool is not installed.
    assert "web_search" not in default_registry().names()
    # Present but engine-less is also not installed.
    config = GaiaConfig(tools={"web_search": ToolConfig()})
    assert "web_search" not in default_registry(config).names()


def test_default_registry_installs_configured_engine() -> None:
    config = GaiaConfig(tools={"web_search": ToolConfig(engine="duckduckgo")})  # type: ignore[call-arg]

    assert "web_search" in default_registry(config).names()


def test_disabled_flag_removes_configured_tool() -> None:
    config = GaiaConfig(
        tools={"web_search": ToolConfig(engine="duckduckgo", enabled=False)}  # type: ignore[call-arg]
    )

    assert "web_search" not in default_registry(config).names()


def test_memory_tools_on_by_default() -> None:
    names = default_registry().names()

    assert "load_memory" in names  # ADK's built-in read tool
    assert "remember" in names  # gaia's explicit write tool


def test_memory_tools_dropped_when_memory_disabled() -> None:
    config = GaiaConfig(memory=MemoryConfig(enabled=False))
    names = default_registry(config).names()

    assert "load_memory" not in names
    assert "remember" not in names


def test_remember_dropped_when_tool_disabled_but_load_memory_kept() -> None:
    config = GaiaConfig(tools={"remember": ToolConfig(enabled=False)})
    names = default_registry(config).names()

    assert "remember" not in names
    assert "load_memory" in names  # gated independently per tool


def test_default_registry_rejects_unknown_engine() -> None:
    config = GaiaConfig(tools={"web_search": ToolConfig(engine="bing")})  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="unknown web_search engine 'bing'"):
        default_registry(config)

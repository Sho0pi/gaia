"""ToolRegistry resolution + default_registry enabled-flag filtering."""

from __future__ import annotations

import pytest

from godpy.config import GodConfig, ToolConfig
from godpy.tools import ToolRegistry, default_registry


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


def test_web_search_not_installed_without_engine() -> None:
    # No config at all: engine is unconfigured, so the tool is not installed.
    assert "web_search" not in default_registry().names()
    # Present but engine-less is also not installed.
    config = GodConfig(tools={"web_search": ToolConfig()})
    assert "web_search" not in default_registry(config).names()


def test_default_registry_installs_configured_engine() -> None:
    config = GodConfig(tools={"web_search": ToolConfig(engine="duckduckgo")})  # type: ignore[call-arg]

    assert "web_search" in default_registry(config).names()


def test_disabled_flag_removes_configured_tool() -> None:
    config = GodConfig(
        tools={"web_search": ToolConfig(engine="duckduckgo", enabled=False)}  # type: ignore[call-arg]
    )

    assert "web_search" not in default_registry(config).names()


def test_default_registry_rejects_unknown_engine() -> None:
    config = GodConfig(tools={"web_search": ToolConfig(engine="bing")})  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="unknown web_search engine 'bing'"):
        default_registry(config)

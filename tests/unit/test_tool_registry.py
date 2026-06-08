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


def test_default_registry_includes_web_search() -> None:
    registry = default_registry()

    assert "web_search" in registry.names()


def test_default_registry_honors_disabled_flag() -> None:
    config = GodConfig(tools={"web_search": ToolConfig(enabled=False)})

    registry = default_registry(config)

    assert "web_search" not in registry.names()


def test_default_registry_enabled_true_is_kept() -> None:
    config = GodConfig(tools={"web_search": ToolConfig(enabled=True)})

    assert "web_search" in default_registry(config).names()

"""Container + Gaia DI/lifecycle wiring.

Singleton semantics (per-Container), laziness (no construction at Gaia init,
only on first access), per-instance isolation (a fresh Gaia builds a fresh
singleton), the memory-disabled gate, and the LifecycleManager teardown
(closer registered only when the resource is pulled).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import gaia.di as di_module
from gaia.config import Settings
from gaia.core import Gaia
from gaia.di import LifecycleManager


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub faster_whisper so the transcriber can build without the real model."""

    class _FakeModel:
        def __init__(self, size: str, device: str, compute_type: str) -> None: ...

    mod = ModuleType("faster_whisper")
    mod.WhisperModel = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


def _gaia(tmp_path: Path) -> Gaia:
    settings = Settings(
        agent_registry_dir=tmp_path / "registry",
        config_path=tmp_path / "gaia.yaml",
        log_dir=tmp_path / "logs",
    )
    return Gaia(settings)


def test_gaia_init_does_not_build_transcriber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Construction must be lazy — touching Gaia.__init__ alone must not call the factory."""
    calls = 0

    def _spy(config: Any) -> Any:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(di_module, "build_transcriber", _spy)

    _gaia(tmp_path)

    assert calls == 0


def test_transcriber_built_on_first_access_and_reused(tmp_path: Path, fake_whisper: None) -> None:
    """``container.transcriber()`` is a lazy singleton: identical instance across calls."""
    gaia = _gaia(tmp_path)

    first = gaia.container.transcriber()
    second = gaia.container.transcriber()

    assert first is not None
    assert first is second


def test_distinct_gaia_instances_have_distinct_transcribers(
    tmp_path: Path, fake_whisper: None
) -> None:
    """The container is per-Gaia, not process-global — fresh Gaia => fresh singleton."""
    one = _gaia(tmp_path / "a")
    two = _gaia(tmp_path / "b")

    assert one.container.transcriber() is not None
    assert two.container.transcriber() is not None
    assert one.container.transcriber() is not two.container.transcriber()


def test_memory_disabled_short_circuits_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When memory.enabled=False, the property returns None without invoking the factory."""
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text("memory:\n  enabled: false\n")
    settings = Settings(
        agent_registry_dir=tmp_path / "registry",
        config_path=config_path,
        log_dir=tmp_path / "logs",
    )

    calls = 0

    def _spy(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("memory service must not be built when memory.enabled=False")

    monkeypatch.setattr(di_module, "_build_memory_service", _spy)

    gaia = Gaia(settings)

    assert gaia.memory_service is None
    assert calls == 0


def test_mcp_toolsets_reused_across_calls(tmp_path: Path) -> None:
    """Pulling the toolsets twice returns the cached singleton list."""
    gaia = _gaia(tmp_path)

    assert gaia.container.mcp_toolsets() is gaia.container.mcp_toolsets()


async def test_lifecycle_manager_runs_registered_closers() -> None:
    """``LifecycleManager`` runs each closer in order and swallows individual failures."""
    calls: list[str] = []

    async def _ok() -> None:
        calls.append("ok")

    async def _fail() -> None:
        calls.append("fail")
        raise RuntimeError("boom")

    lifecycle = LifecycleManager()
    lifecycle.add(_ok)
    lifecycle.add(_fail)
    lifecycle.add(_ok)

    await lifecycle.aclose()

    assert calls == ["ok", "fail", "ok"]


async def test_close_runs_no_closers_when_resources_never_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If nothing pulls mcp/skill toolsets, ``Gaia.close()`` never invokes a closer."""
    closed: list[str] = []

    async def _spy_close_mcp(_toolsets: Any) -> None:
        closed.append("mcp")

    monkeypatch.setattr("gaia.mcp.close_mcp_toolsets", _spy_close_mcp)

    gaia = _gaia(tmp_path)
    # No access to gaia.container.mcp_toolsets() / .skill_toolsets() — never built.

    await gaia.close()

    assert closed == []


async def test_close_runs_closers_for_pulled_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pulling mcp_toolsets registers a closer; close runs it exactly once."""
    fake_toolsets = [object()]  # truthy so the real `_build_mcp_toolsets` registers a closer
    closed: list[Any] = []

    async def _spy_close_mcp(toolsets: Any) -> None:
        closed.append(toolsets)

    # Patch the builder + closer at their import path inside `gaia.mcp`, which is
    # what `_build_mcp_toolsets` reaches through. Declarative containers capture
    # the wrapper at class-definition, so patching `di._build_mcp_toolsets`
    # itself would be a no-op.
    monkeypatch.setattr("gaia.mcp.build_mcp_toolsets", lambda _cfg: fake_toolsets)
    monkeypatch.setattr("gaia.mcp.close_mcp_toolsets", _spy_close_mcp)

    gaia = _gaia(tmp_path)
    pulled = gaia.container.mcp_toolsets()

    await gaia.close()

    assert pulled is fake_toolsets
    assert closed == [fake_toolsets]

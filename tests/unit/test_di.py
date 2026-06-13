"""Container + Gaia DI/lifecycle wiring.

Singleton semantics (per-Container), laziness (no construction at Gaia init,
only on first access), per-instance isolation (a fresh Gaia builds a fresh
singleton), and the memory-disabled gate.
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
    """``gaia.transcriber`` is a lazy singleton: identical instance across calls."""
    gaia = _gaia(tmp_path)

    first = gaia.transcriber
    second = gaia.transcriber

    assert first is not None
    assert first is second


def test_distinct_gaia_instances_have_distinct_transcribers(
    tmp_path: Path, fake_whisper: None
) -> None:
    """The container is per-Gaia, not process-global — fresh Gaia => fresh singleton."""
    one = _gaia(tmp_path / "a")
    two = _gaia(tmp_path / "b")

    assert one.transcriber is not None
    assert two.transcriber is not None
    assert one.transcriber is not two.transcriber


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
    """The mcp toolsets list is the same object on every Gaia.mcp_toolsets() call."""
    gaia = _gaia(tmp_path)

    assert gaia.mcp_toolsets() is gaia.mcp_toolsets()

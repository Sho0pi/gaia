"""Transcriber: lazy model, segment joining, config gating — faster_whisper faked."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import gaia.voice as voice_mod
from gaia.config import GaiaConfig
from gaia.voice import Transcriber, build_transcriber


class _FakeModel:
    instances: int = 0

    def __init__(self, size: str, device: str, compute_type: str) -> None:
        _FakeModel.instances += 1
        self.size = size
        self.kwargs: list[dict[str, Any]] = []

    def transcribe(self, path: str, language: str | None = None) -> tuple[list[Any], Any]:
        self.kwargs.append({"path": path, "language": language})
        segments = [SimpleNamespace(text=" hello "), SimpleNamespace(text="world ")]
        return segments, SimpleNamespace(language=language or "en")


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> type[_FakeModel]:
    _FakeModel.instances = 0
    mod = ModuleType("faster_whisper")
    mod.WhisperModel = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    return _FakeModel


async def test_transcribe_joins_segments(fake_whisper: type[_FakeModel], tmp_path: Path) -> None:
    text = await Transcriber(model="tiny").transcribe(tmp_path / "note.ogg")

    assert text == "hello world"


async def test_model_built_once_across_calls(
    fake_whisper: type[_FakeModel], tmp_path: Path
) -> None:
    transcriber = Transcriber()

    await transcriber.transcribe(tmp_path / "a.ogg")
    await transcriber.transcribe(tmp_path / "b.ogg")

    assert fake_whisper.instances == 1  # cached on the instance, loaded once


async def test_language_passed_through(fake_whisper: type[_FakeModel], tmp_path: Path) -> None:
    transcriber = Transcriber(language="he")

    await transcriber.transcribe(tmp_path / "a.ogg")

    assert transcriber._model.kwargs[0]["language"] == "he"


def test_available_false_without_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    # Evict any prior import (e.g. from fake_whisper used in earlier tests via to_thread)
    # before patching find_spec — the available property checks sys.modules first.
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    monkeypatch.setattr(voice_mod.importlib.util, "find_spec", lambda name: None)

    assert Transcriber().available is False


def test_build_transcriber_disabled_returns_none() -> None:
    cfg = GaiaConfig.model_validate({"voice": {"enabled": False}})

    assert build_transcriber(cfg) is None


def test_build_transcriber_missing_dep_warns_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    monkeypatch.setattr(voice_mod.importlib.util, "find_spec", lambda name: None)

    import logging

    with caplog.at_level(logging.WARNING, logger="gaia.voice"):
        result = build_transcriber(GaiaConfig())

    assert result is None
    assert "uv sync --group voice" in caplog.text


def test_build_transcriber_returns_fresh_instances(fake_whisper: type[_FakeModel]) -> None:
    # No module singleton: each call builds its own Transcriber (the caller owns it).
    first = build_transcriber(GaiaConfig())
    second = build_transcriber(GaiaConfig())

    assert first is not None and second is not None and first is not second


def test_build_transcriber_passes_device_and_compute_type(fake_whisper: type[_FakeModel]) -> None:
    cfg = GaiaConfig.model_validate({"voice": {"device": "cuda", "compute_type": "float16"}})

    transcriber = build_transcriber(cfg)

    assert transcriber is not None
    assert transcriber._device == "cuda"
    assert transcriber._compute_type == "float16"

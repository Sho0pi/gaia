"""System test: real piper TTS renders text to an ogg/opus voice note.

Triple-gated so CI stays green: needs the optional 'voice' dep group (piper), the
``espeak-ng`` binary on PATH, and GAIA_VOICE_RUN_LIVE (the first run downloads a piper
voice model — network + a few MB).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.system

pytest.importorskip("piper", reason="needs the optional 'voice' dep group")


@pytest.mark.skipif(
    shutil.which("espeak-ng") is None, reason="piper needs the espeak-ng binary on PATH"
)
@pytest.mark.skipif(
    not os.environ.get("GAIA_VOICE_RUN_LIVE"),
    reason="downloads a piper voice model; set GAIA_VOICE_RUN_LIVE to run",
)
async def test_synthesizer_renders_ogg_voice_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaia import constants
    from gaia.voice import Synthesizer

    monkeypatch.setattr(constants, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("gaia.voice._PIPER_DIR", tmp_path / "piper")

    synth = Synthesizer()
    assert synth.available
    ogg = await synth.synthesize("Hello, this is a gaia voice reply.")

    assert ogg is not None and ogg.exists() and ogg.suffix == ".ogg"
    assert ogg.read_bytes()[:4] == b"OggS"  # Ogg container magic
    assert ogg.stat().st_size > 1000

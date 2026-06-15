"""System test: real edge-tts renders text to an ogg/opus voice note.

Double-gated so CI stays green: needs the optional 'voice' dep group (edge-tts) and
GAIA_VOICE_RUN_LIVE (synthesis is a live network call to Microsoft Edge's TTS service).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.system

pytest.importorskip("edge_tts", reason="needs the optional 'voice' dep group")


@pytest.mark.skipif(
    not os.environ.get("GAIA_VOICE_RUN_LIVE"),
    reason="calls the edge-tts network service; set GAIA_VOICE_RUN_LIVE to run",
)
async def test_synthesizer_renders_ogg_voice_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaia import constants
    from gaia.voice import Synthesizer

    monkeypatch.setattr(constants, "CACHE_DIR", tmp_path / "cache")

    synth = Synthesizer()
    assert synth.available
    ogg = await synth.synthesize("Hello, this is a gaia voice reply.")

    assert ogg is not None and ogg.exists() and ogg.suffix == ".ogg"
    assert ogg.read_bytes()[:4] == b"OggS"  # Ogg container magic
    assert ogg.stat().st_size > 1000

"""System test: real faster-whisper transcribes a generated wav.

Double-gated: needs the optional 'voice' dep group, and GAIA_VOICE_RUN_LIVE (the first
run downloads the tiny model weights from Hugging Face — network + ~75 MB).
"""

from __future__ import annotations

import math
import os
import struct
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.system

pytest.importorskip("faster_whisper", reason="needs the optional 'voice' dep group")


@pytest.mark.skipif(
    not os.environ.get("GAIA_VOICE_RUN_LIVE"),
    reason="downloads whisper weights from HF; set GAIA_VOICE_RUN_LIVE to run",
)
async def test_transcriber_runs_on_real_audio(tmp_path: Path) -> None:
    from gaia.voice import Transcriber

    # One second of 440 Hz sine at 16 kHz mono — decodable, content-free audio.
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(16000)
        )
        wav.writeframes(frames)

    text = await Transcriber(model="tiny").transcribe(path)

    assert isinstance(text, str)  # a tone may transcribe to "" — the contract is no crash

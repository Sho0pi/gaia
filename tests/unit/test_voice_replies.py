"""Voice replies: build_synthesizer gating + the connector speaks text back when voice-in."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.config import GaiaConfig
from gaia.connectors.whatsapp_web import WhatsAppWebConnector
from gaia.voice import Synthesizer, build_synthesizer


class _FakeSynth:
    def __init__(self, out: Path | None) -> None:
        self._out = out
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> Path | None:
        self.texts.append(text)
        return self._out


class _FakeClient:
    def __init__(self) -> None:
        self.audio: list[tuple[Any, str, bool]] = []
        self.text: list[str] = []

    async def build_audio_message(self, file: str, ptt: bool = False) -> SimpleNamespace:
        # Mirror neonize: a Message with an audioMessage carrying a (wrong) sniffed mimetype.
        self.audio.append(("__build__", file, ptt))
        return SimpleNamespace(
            audioMessage=SimpleNamespace(mimetype="audio/ogg", PTT=ptt, _file=file)
        )

    async def send_message(self, chat: Any, msg: Any) -> None:
        self.audio.append((chat, msg.audioMessage._file, msg.audioMessage.mimetype))

    async def reply_message(self, text: str, message: Any) -> None:
        self.text.append(text)


def _connector(synth: Any) -> WhatsAppWebConnector:
    return WhatsAppWebConnector(Path("/tmp/wa.db"), _dispatch, synthesizer=synth)


async def _dispatch(*_a: Any, **_k: Any) -> None:  # unused stub
    return None


# -- build_synthesizer gating ----------------------------------------------------------


def test_build_synthesizer_off_when_reply_with_voice_false() -> None:
    cfg = GaiaConfig.model_validate({"voice": {"enabled": True, "reply_with_voice": False}})
    assert build_synthesizer(cfg) is None


def test_build_synthesizer_off_when_voice_disabled() -> None:
    cfg = GaiaConfig.model_validate({"voice": {"enabled": False}})
    assert build_synthesizer(cfg) is None


def test_build_synthesizer_none_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # piper / espeak-ng missing → degrade to text (warned), like the whisper gate.
    monkeypatch.setattr(Synthesizer, "available", property(lambda _self: False))
    assert build_synthesizer(GaiaConfig()) is None


def test_build_synthesizer_built_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Synthesizer, "available", property(lambda _self: True))
    synth = build_synthesizer(
        GaiaConfig.model_validate({"voice": {"tts_voice": "en_US-amy-medium"}})
    )
    assert isinstance(synth, Synthesizer) and synth._voice == "en_US-amy-medium"


# -- the connector speaks text replies back as a voice note (voice-in only) -------------


async def test_speak_sends_ptt_audio(tmp_path: Path) -> None:
    ogg = tmp_path / "r.ogg"
    ogg.write_bytes(b"OggS")
    client = _FakeClient()
    spoke = await _connector(_FakeSynth(ogg))._speak(client, "chat-jid", "hello there")

    assert spoke is True
    # built as PTT, then sent with the WhatsApp-required opus mimetype (corrected from the
    # bare 'audio/ogg' neonize would otherwise sniff — which silently fails to render).
    assert client.audio == [
        ("__build__", str(ogg), True),
        ("chat-jid", str(ogg), "audio/ogg; codecs=opus"),
    ]


async def test_speak_falls_back_when_synthesis_fails(tmp_path: Path) -> None:
    client = _FakeClient()
    spoke = await _connector(_FakeSynth(None))._speak(client, "chat-jid", "hello")

    assert spoke is False and client.audio == []  # caller will send text instead


async def test_speak_without_synthesizer_is_false() -> None:
    spoke = await _connector(None)._speak(_FakeClient(), "chat-jid", "hi")
    assert spoke is False


async def test_speak_empty_text_is_false() -> None:
    spoke = await _connector(_FakeSynth(Path("/x.ogg")))._speak(_FakeClient(), "chat-jid", "  ")
    assert spoke is False

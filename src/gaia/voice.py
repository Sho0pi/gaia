"""Local voice I/O: speech-to-text in (faster-whisper) and text-to-speech out (edge-tts).

Connector-agnostic. **In:** a connector downloads the audio (WhatsApp voice notes are
ogg/opus — faster-whisper decodes them natively through its bundled PyAV, no ffmpeg binary)
and hands the file to :class:`Transcriber`. **Out:** :class:`Synthesizer` turns a text reply
into an ogg/opus voice note (edge-tts → MP3 → PyAV transcode), so a voice message is
answered with a voice message.

Both engines are optional (the ``voice`` dep group) and imported lazily, so gaia imports
cleanly without them — callers check ``.available`` and degrade to text, like the
fd/rg/playwright gates. edge-tts speaks via Microsoft Edge's online voices, so it needs
network access but no local model or binary.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import GaiaConfig

logger = logging.getLogger(__name__)


class Transcriber:
    """Speech-to-text over a lazily-built, instance-cached WhisperModel.

    The model is held on the instance (built on first :meth:`transcribe`), so one
    Transcriber loads the weights once and reuses them. The instance itself is a
    lazy singleton on :attr:`gaia.core.Gaia.transcriber` (via
    :class:`gaia.di.Container`); connectors receive it via constructor injection
    from the composition root (``app.py``).
    """

    def __init__(
        self,
        model: str = "base",
        language: str | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        # device: where CTranslate2 runs the model — "cpu" (anywhere) or "cuda" (NVIDIA GPU).
        # compute_type: weight quantisation — "int8" is the smallest/fastest on CPU (8-bit
        # integer math, ~Pi-viable); GPUs typically pair "cuda" with "float16".
        self._model_size = model
        self._language = language
        self._device = device
        self._compute_type = compute_type
        self._model: Any = None

    @property
    def available(self) -> bool:
        """True when faster-whisper is installed (the 'voice' dep group)."""
        if "faster_whisper" in sys.modules:  # already imported (or test-injected)
            return True
        return importlib.util.find_spec("faster_whisper") is not None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "loading whisper model %r on %s (first call may download weights)",
                self._model_size,
                self._device,
            )
            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    async def transcribe(self, path: Path) -> str:
        """The spoken text of the audio file at ``path`` (empty string when silent).

        Loading the model and running inference are *synchronous, CPU-bound* CTranslate2
        calls that would block the event loop (freezing the connector — no other messages
        get handled) for the whole transcription. ``asyncio.to_thread`` runs that blocking
        work on a worker thread and ``await``s its result, so this coroutine yields the
        loop back to other tasks until the transcript is ready.
        """

        def _run() -> str:
            model = self._ensure_model()
            segments, _info = model.transcribe(str(path), language=self._language)
            return " ".join(segment.text.strip() for segment in segments).strip()

        return await asyncio.to_thread(_run)


class Synthesizer:
    """Text-to-speech over edge-tts (Microsoft Edge's online neural voices).

    A lazy singleton on :attr:`gaia.core.Gaia.synthesizer`; connectors receive it via
    constructor injection. Each call streams the spoken MP3 from Edge's service, then PyAV
    transcodes it to an ogg/opus voice note — the format WhatsApp wants for a PTT message.
    No local model or binary: synthesis is a network call, so it needs connectivity.
    """

    def __init__(self, voice: str = "en-US-AriaNeural") -> None:
        self._voice = voice

    @property
    def available(self) -> bool:
        """True when edge-tts is installed (the 'voice' dep group)."""
        if "edge_tts" in sys.modules:  # already imported (or test-injected)
            return True
        return importlib.util.find_spec("edge_tts") is not None

    async def synthesize(self, text: str) -> Path | None:
        """Render ``text`` to an ogg/opus voice note; the file path, or ``None`` on failure.

        edge-tts speaks over the network (an async call, awaited directly); the PyAV
        transcode is synchronous and CPU-bound, so it runs on a worker thread (like
        :meth:`Transcriber.transcribe`) to keep the connector loop live.
        """
        cleaned = text.strip()
        if not cleaned:
            return None

        try:
            import edge_tts

            constants.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            stem = constants.CACHE_DIR / f"tts-{abs(hash(cleaned)) & 0xFFFFFFF:x}"
            mp3_path = stem.with_suffix(".mp3")
            await edge_tts.Communicate(cleaned, self._voice).save(str(mp3_path))
            return await asyncio.to_thread(_audio_to_ogg, mp3_path, stem.with_suffix(".ogg"))
        except Exception:  # never take the connector loop down for a bad TTS
            logger.warning("voice reply synthesis failed", exc_info=True)
            return None


def _audio_to_ogg(src_path: Path, ogg_path: Path) -> Path:
    """Transcode an audio file to ogg/opus with PyAV (bundled by faster-whisper, no ffmpeg).

    A WhatsApp PTT voice note wants **mono** opus at 48 kHz (opus' native rate); edge-tts
    yields a 24 kHz MP3, so we resample explicitly to mono/48k. The source file is removed
    once transcoded.
    """
    import av
    from av.audio.resampler import AudioResampler

    resampler = AudioResampler(format="s16", layout="mono", rate=48000)
    with av.open(str(src_path)) as src, av.open(str(ogg_path), "w", format="ogg") as dst:
        in_stream = src.streams.audio[0]
        out_stream = dst.add_stream("libopus", rate=48000, layout="mono")
        for frame in src.decode(in_stream):
            for rframe in resampler.resample(frame):
                for packet in out_stream.encode(rframe):
                    dst.mux(packet)
        for rframe in resampler.resample(None):  # flush the resampler
            for packet in out_stream.encode(rframe):
                dst.mux(packet)
        for packet in out_stream.encode(None):  # flush the encoder
            dst.mux(packet)
    src_path.unlink(missing_ok=True)
    return ogg_path


def build_synthesizer(config: GaiaConfig) -> Synthesizer | None:
    """Build a TTS synthesizer from ``voice`` config, or ``None`` when off/unavailable.

    ``None`` when voice replies are disabled, or edge-tts isn't installed (warned, naming
    the remedy) — connectors then reply with text, today's behaviour.
    """
    if not config.voice.enabled or not config.voice.reply_with_voice:
        return None
    synth = Synthesizer(voice=config.voice.tts_voice)
    if not synth.available:
        logger.warning("voice replies off: edge-tts not installed (run 'uv sync --group voice')")
        return None
    return synth


def build_transcriber(config: GaiaConfig) -> Transcriber | None:
    """Build a transcriber from ``voice`` config, or ``None`` when off/uninstalled.

    A plain factory — no caching, no module global. ``gaia.di.Container`` calls
    it once per Gaia (``providers.Singleton``) so ``Gaia.transcriber`` is the
    same instance for every caller; the model is then loaded once and reused on
    that instance. ``None`` when ``voice.enabled`` is false or faster-whisper
    isn't installed (warned, naming the remedy) — connectors then ignore voice
    notes, today's behaviour.
    """
    if not config.voice.enabled:
        return None
    transcriber = Transcriber(
        model=config.voice.model,
        language=config.voice.language,
        device=config.voice.device,
        compute_type=config.voice.compute_type,
    )
    if not transcriber.available:
        logger.warning(
            "voice notes ignored: faster-whisper not installed (run 'uv sync --group voice')"
        )
        return None
    return transcriber

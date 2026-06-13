"""Local voice I/O: speech-to-text in (faster-whisper) and text-to-speech out (piper).

Connector-agnostic. **In:** a connector downloads the audio (WhatsApp voice notes are
ogg/opus — faster-whisper decodes them natively through its bundled PyAV, no ffmpeg binary)
and hands the file to :class:`Transcriber`. **Out:** :class:`Synthesizer` turns a text reply
into an ogg/opus voice note (piper → WAV → PyAV transcode), so a voice message is answered
with a voice message.

Both engines are optional (the ``voice`` dep group) and imported lazily, so gaia imports
cleanly without them — callers check ``.available`` and degrade to text, like the
fd/rg/playwright gates. piper additionally needs the ``espeak-ng`` binary on ``PATH``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import shutil
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


#: Where piper voice models (.onnx + .onnx.json) are cached / auto-downloaded.
_PIPER_DIR = constants.HOME_DIR / "piper"


class Synthesizer:
    """Text-to-speech over a lazily-built, instance-cached piper voice.

    The voice model is held on the instance (built on first :meth:`synthesize`, downloaded
    once if missing), so one Synthesizer loads it once and reuses it. A lazy singleton on
    :attr:`gaia.core.Gaia.synthesizer`; connectors receive it via constructor injection.
    Output is an ogg/opus voice note (piper renders WAV, PyAV transcodes) — the format
    WhatsApp wants for a PTT message.
    """

    def __init__(self, voice: str = "en_US-lessac-medium") -> None:
        self._voice = voice
        self._model: Any = None

    @property
    def available(self) -> bool:
        """True when piper (the 'voice' group) AND its espeak-ng binary are present."""
        has_piper = "piper" in sys.modules or importlib.util.find_spec("piper") is not None
        return has_piper and shutil.which("espeak-ng") is not None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from piper import PiperVoice
            from piper.download_voices import download_voice

            _PIPER_DIR.mkdir(parents=True, exist_ok=True)
            onnx = _PIPER_DIR / f"{self._voice}.onnx"
            if not onnx.is_file():
                logger.info("downloading piper voice %r (first use)", self._voice)
                download_voice(self._voice, _PIPER_DIR)
            self._model = PiperVoice.load(str(onnx))
        return self._model

    async def synthesize(self, text: str) -> Path | None:
        """Render ``text`` to an ogg/opus voice note; the file path, or ``None`` on failure.

        piper synthesis + the PyAV transcode are synchronous and CPU-bound, so they run on a
        worker thread (like :meth:`Transcriber.transcribe`) to keep the connector loop live.
        """
        cleaned = text.strip()
        if not cleaned:
            return None

        def _run() -> Path | None:
            import wave

            voice = self._ensure_model()
            constants.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            stem = constants.CACHE_DIR / f"tts-{abs(hash(cleaned)) & 0xFFFFFFF:x}"
            wav_path = stem.with_suffix(".wav")
            with wave.open(str(wav_path), "wb") as wav:
                voice.synthesize_wav(cleaned, wav)
            return _wav_to_ogg(wav_path, stem.with_suffix(".ogg"))

        try:
            return await asyncio.to_thread(_run)
        except Exception:  # never take the connector loop down for a bad TTS
            logger.warning("voice reply synthesis failed", exc_info=True)
            return None


def _wav_to_ogg(wav_path: Path, ogg_path: Path) -> Path:
    """Transcode a WAV file to ogg/opus with PyAV (bundled by faster-whisper, no ffmpeg)."""
    import av

    with av.open(str(wav_path)) as src, av.open(str(ogg_path), "w", format="ogg") as dst:
        in_stream = src.streams.audio[0]
        out_stream = dst.add_stream("libopus")
        for frame in src.decode(in_stream):
            for packet in out_stream.encode(frame):
                dst.mux(packet)
        for packet in out_stream.encode(None):  # flush
            dst.mux(packet)
    wav_path.unlink(missing_ok=True)
    return ogg_path


def build_synthesizer(config: GaiaConfig) -> Synthesizer | None:
    """Build a TTS synthesizer from ``voice`` config, or ``None`` when off/unavailable.

    ``None`` when voice replies are disabled, or piper/espeak-ng aren't installed (warned,
    naming the remedy) — connectors then reply with text, today's behaviour.
    """
    if not config.voice.enabled or not config.voice.reply_with_voice:
        return None
    synth = Synthesizer(voice=config.voice.tts_voice)
    if not synth.available:
        logger.warning(
            "voice replies off: piper TTS unavailable (run 'uv sync --group voice' and "
            "install espeak-ng, e.g. 'brew install espeak-ng')"
        )
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

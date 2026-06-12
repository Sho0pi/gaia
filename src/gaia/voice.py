"""Local speech-to-text for inbound voice messages (faster-whisper).

Connector-agnostic: a connector downloads the audio (WhatsApp voice notes are
ogg/opus — faster-whisper decodes them natively through its bundled PyAV, no ffmpeg
binary) and hands the file here. The model is CTranslate2 Whisper running on CPU
(``compute_type="int8"`` — Pi-viable); weights auto-download from Hugging Face on the
first transcription and are cached under ``~/.cache``.

faster-whisper is an optional dependency (the ``voice`` group) and is imported lazily,
so gaia imports cleanly without it — callers check :attr:`Transcriber.available` and
degrade with a warning, like the fd/rg/playwright gates.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import GaiaConfig

logger = logging.getLogger(__name__)


class Transcriber:
    """Speech-to-text over a lazily-built, instance-cached WhisperModel."""

    def __init__(self, model: str = "base", language: str | None = None) -> None:
        self._model_size = model
        self._language = language
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
                "loading whisper model %r (first call may download weights)", self._model_size
            )
            self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
        return self._model

    async def transcribe(self, path: Path) -> str:
        """The spoken text of the audio file at ``path`` (empty string when silent).

        Model load + inference are blocking CTranslate2 calls, so both run in a worker
        thread; the event loop (and the connector receiving messages) stays free.
        """

        def _run() -> str:
            model = self._ensure_model()
            segments, _info = model.transcribe(str(path), language=self._language)
            return " ".join(segment.text.strip() for segment in segments).strip()

        return await asyncio.to_thread(_run)


_transcriber: Transcriber | None = None


def get_transcriber(config: GaiaConfig) -> Transcriber | None:
    """The process-wide transcriber per ``voice`` config, or ``None`` when off/missing.

    ``None`` when ``voice.enabled`` is false or faster-whisper isn't installed (warned
    once, naming the remedy) — connectors then ignore voice notes, today's behaviour.
    """
    global _transcriber
    if not config.voice.enabled:
        return None
    if _transcriber is None:
        candidate = Transcriber(model=config.voice.model, language=config.voice.language)
        if not candidate.available:
            logger.warning(
                "voice notes ignored: faster-whisper not installed (run 'uv sync --group voice')"
            )
            return None
        _transcriber = candidate
    return _transcriber

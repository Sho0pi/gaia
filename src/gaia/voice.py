"""Local speech-to-text for inbound voice messages (faster-whisper).

Connector-agnostic: a connector downloads the audio (WhatsApp voice notes are
ogg/opus — faster-whisper decodes them natively through its bundled PyAV, no ffmpeg
binary) and hands the file here. The model is CTranslate2 Whisper; weights auto-download
from Hugging Face on the first transcription and are cached under ``~/.cache``. The
``device``/``compute_type`` default to ``cpu``/``int8`` (runs anywhere, down to a Pi)
but are config-driven, so a machine with a GPU can set ``cuda``/``float16`` for speed.

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

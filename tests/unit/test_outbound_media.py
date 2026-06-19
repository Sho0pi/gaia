"""Outbound media: Media.kind inference and the telegram per-kind send helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gaia.connectors.base import Media, media_kind
from gaia.connectors.telegram import _tg_reply_media, _tg_send_media


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("photo.png", "image"),
        ("photo.JPG", "image"),
        ("clip.mp4", "video"),
        ("song.mp3", "audio"),
        ("voice.ogg", "audio"),
        ("report.pdf", "document"),
        ("data.csv", "document"),
        ("mystery.unknownext", "document"),  # unknown → safe document default
    ],
)
def test_media_kind(name: str, kind: str) -> None:
    assert media_kind(Path(name)) == kind


def test_media_infers_kind_when_blank() -> None:
    assert Media(Path("/tmp/a.pdf")).kind == "document"
    assert Media(Path("/tmp/a.png")).kind == "image"
    assert Media(Path("/tmp/a.png"), kind="document").kind == "document"  # explicit wins


class _FakeMessage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, str | None]] = []

    def _record(self, method: str) -> Any:
        async def call(file: Path, caption: str | None = None) -> None:
            self.calls.append((method, file, caption))

        return call

    def __getattr__(self, name: str) -> Any:
        if name.startswith("reply_"):
            return self._record(name)
        raise AttributeError(name)


@pytest.mark.parametrize(
    ("name", "method"),
    [
        ("a.png", "reply_photo"),
        ("a.mp4", "reply_video"),
        ("a.mp3", "reply_audio"),
        ("a.pdf", "reply_document"),
    ],
)
async def test_tg_reply_media_picks_method_by_kind(name: str, method: str) -> None:
    msg = _FakeMessage()
    await _tg_reply_media(msg, Media(Path("/tmp") / name, caption="cap"))
    assert msg.calls == [(method, Path("/tmp") / name, "cap")]


async def test_tg_send_media_picks_method_by_kind() -> None:
    sends: list[tuple[str, str, Path, str | None]] = []

    class _Bot:
        def __getattr__(self, name: str) -> Any:
            async def call(chat: str, file: Path, caption: str | None = None) -> None:
                sends.append((name, chat, file, caption))

            return call

    await _tg_send_media(_Bot(), "chat1", Media(Path("/tmp/x.pdf"), caption="doc"))
    assert sends == [("send_document", "chat1", Path("/tmp/x.pdf"), "doc")]

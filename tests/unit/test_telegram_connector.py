"""`TelegramConnector` — setMyCommands registration (#62)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("telegram")

from gaia.connectors.telegram import TelegramConnector


def _fake_app() -> Any:
    """A stand-in Application whose bot records set_my_commands calls."""
    calls: list[Any] = []

    async def set_my_commands(menu: Any) -> None:
        calls.append(menu)

    return SimpleNamespace(bot=SimpleNamespace(set_my_commands=set_my_commands, calls=calls))


async def _noop_dispatch(*_a: Any, **_k: Any) -> None:  # pragma: no cover
    return None


async def test_register_commands_sends_valid_menu() -> None:
    conn = TelegramConnector(
        "tok", _noop_dispatch, commands=[("help", "Show help"), ("reset", "X")]
    )
    app = _fake_app()

    await conn._register_commands(app)

    sent = app.bot.calls[0]
    assert [(c.command, c.description) for c in sent] == [("help", "Show help"), ("reset", "X")]


async def test_register_commands_skips_non_conforming_names() -> None:
    conn = TelegramConnector("tok", _noop_dispatch, commands=[("bad-name", "x"), ("ok", "y")])
    app = _fake_app()

    await conn._register_commands(app)

    assert [c.command for c in app.bot.calls[0]] == ["ok"]  # dashed name dropped


async def test_register_commands_caps_description_at_256() -> None:
    conn = TelegramConnector("tok", _noop_dispatch, commands=[("help", "z" * 300)])
    app = _fake_app()

    await conn._register_commands(app)

    assert len(app.bot.calls[0][0].description) == 256


async def test_register_commands_noop_without_commands() -> None:
    conn = TelegramConnector("tok", _noop_dispatch)  # no commands passed
    app = _fake_app()

    await conn._register_commands(app)

    assert app.bot.calls == []  # set_my_commands never called


class _FakeFile:
    async def download_to_drive(self, custom_path: str) -> None:  # the bytes don't matter here
        return None


class _FakeTranscriber:
    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe(self, path: Any) -> str:
        return self._text


def _voice_message(mid: int = 1) -> Any:
    async def get_file() -> Any:
        return _FakeFile()

    return SimpleNamespace(voice=SimpleNamespace(get_file=get_file), audio=None, message_id=mid)


async def test_transcribe_prefixes_voice_text() -> None:
    conn = TelegramConnector("tok", _noop_dispatch, transcriber=_FakeTranscriber("hi there"))
    assert await conn._transcribe(_voice_message()) == "[voice message] hi there"


async def test_transcribe_empty_without_transcriber_or_when_silent() -> None:
    assert await TelegramConnector("tok", _noop_dispatch)._transcribe(_voice_message()) == ""
    silent = TelegramConnector("tok", _noop_dispatch, transcriber=_FakeTranscriber(""))
    assert await silent._transcribe(_voice_message()) == ""


def _photo_message(mid: int = 2) -> Any:
    async def get_file() -> Any:
        return _FakeFile()

    return SimpleNamespace(
        photo=[SimpleNamespace(get_file=get_file)], document=None, video=None, message_id=mid
    )


def _doc_message(mime: str, fname: str, mid: int = 3) -> Any:
    async def get_file() -> Any:
        return _FakeFile()

    doc = SimpleNamespace(get_file=get_file, mime_type=mime, file_name=fname)
    return SimpleNamespace(photo=None, document=doc, video=None, message_id=mid)


async def test_download_photo_is_image() -> None:
    item = await TelegramConnector("tok", _noop_dispatch)._download(_photo_message())
    assert item is not None and item.kind == "image" and item.mime == "image/jpeg"


async def test_download_document_kind_from_mime() -> None:
    conn = TelegramConnector("tok", _noop_dispatch)
    pdf = await conn._download(_doc_message("application/pdf", "report.pdf"))
    assert pdf is not None and pdf.kind == "document"
    png = await conn._download(_doc_message("image/png", "shot.png"))
    assert png is not None and png.kind == "image"  # an image sent as a file is still an image


async def test_question_renders_inline_keyboard() -> None:
    from gaia.connectors.base import Question

    captured: dict[str, Any] = {}

    async def reply_text(text: str, reply_markup: Any = None) -> None:
        captured["text"], captured["markup"] = text, reply_markup

    message = SimpleNamespace(chat_id=99, reply_text=reply_text)
    conn = TelegramConnector("tok", _noop_dispatch)

    await conn._send_reply(message, Question(text="Pick one", options=("Red", "Blue")))

    assert captured["text"] == "Pick one"
    rows = captured["markup"].inline_keyboard  # InlineKeyboardMarkup
    assert [r[0].text for r in rows] == ["Red", "Blue"]
    assert [r[0].callback_data for r in rows] == ["0", "1"]  # index → label on a tap
    assert conn._choices[99] == ("Red", "Blue")


async def test_run_turn_feeds_the_selected_answer() -> None:
    captured: dict[str, Any] = {}

    async def dispatch(_sid: str, _name: str, inbound: Any, _send: Any) -> None:
        captured["text"] = inbound.text

    async def send_chat_action(**_k: Any) -> None:
        return None

    conn = TelegramConnector("tok", dispatch)
    message = SimpleNamespace(chat_id=99, chat=SimpleNamespace(type="private"))
    sender = SimpleNamespace(id=5, first_name="Itay", username=None)
    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=send_chat_action))

    await conn._run_turn(context, message, sender, "[Selected: Red]")
    assert captured["text"] == "[Selected: Red]"

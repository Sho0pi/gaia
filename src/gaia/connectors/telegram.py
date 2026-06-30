"""Telegram adapter built on python-telegram-bot (async).

Deferred import keeps the connector module importable without the telegram dep
installed, so unit tests can exercise wiring without a live bot.

Inbound text, voice (transcribed locally), and media (image/video/document) all flow to the
dispatcher; while a turn runs, a "typing…" chat action is kept alive. Outbound replies go out as
text (chunked past Telegram's 4096 cap) or the real file by media kind.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.connectors.base import (
    TELEGRAM_LIMIT,
    Dispatch,
    Inbound,
    InboundMedia,
    Media,
    Reply,
    as_text,
    chunk_text,
    current_chat,
)
from gaia.logs import log_error

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.voice import Transcriber

#: Telegram bot-command names must fully match this; non-conforming ones are skipped in the menu.
_TG_COMMAND_NAME = re.compile(r"[a-z0-9_]{1,32}")

#: Media.kind → (Message.reply_* method, Bot.send_* method). python-telegram-bot accepts a
#: ``Path`` as the file argument and opens it itself.
_TG_SENDERS = {
    "image": ("reply_photo", "send_photo"),
    "video": ("reply_video", "send_video"),
    "audio": ("reply_audio", "send_audio"),
    "document": ("reply_document", "send_document"),
}


def _media_kind(mime: str) -> str:
    """Classify a downloaded attachment by mime top-level type (document is the safe default)."""
    top = (mime or "").split("/")[0]
    return top if top in ("image", "video", "audio") else "document"


async def _tg_reply_media(message: Any, media: Media) -> None:
    """Reply with ``media`` using the method matching its kind (document as default)."""
    reply_method, _ = _TG_SENDERS.get(media.kind, _TG_SENDERS["document"])
    await getattr(message, reply_method)(media.path, caption=media.caption or None)


async def _tg_send_media(bot: Any, chat: str, media: Media) -> None:
    """Proactively send ``media`` to ``chat`` using the Bot.send_* matching its kind."""
    _, send_method = _TG_SENDERS.get(media.kind, _TG_SENDERS["document"])
    await getattr(bot, send_method)(chat, media.path, caption=media.caption or None)


class TelegramConnector:
    """Bridges Telegram messages to the dispatcher (per-sender identity → user)."""

    #: Connector id used in cron job channel fields / the daemon's connector registry.
    NAME = "telegram"

    def __init__(
        self,
        token: str,
        dispatch: Dispatch,
        commands: list[tuple[str, str]] | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self._token = token
        self._dispatch = dispatch  # channel-bound: (sender_id, name, inbound, send)
        # (name, summary) pairs registered via setMyCommands so typing '/' shows a menu. Plain
        # tuples — the connector stays a dumb pipe; the caller flattens the command registry.
        self._commands = commands or []
        # Turns inbound voice notes into text; None = voice messages are ignored (no transcriber).
        self._transcriber = transcriber
        self._app: Any = None  # the live Application while start() runs (for send_to)

    def build_application(self) -> object:
        """Create a python-telegram-bot Application wired to the dispatcher."""
        from telegram import Update
        from telegram.ext import Application, MessageHandler, filters

        app = Application.builder().token(self._token).build()

        async def _on_message(update: Update, context: Any) -> None:
            message = update.message
            if not message or not message.from_user:
                return
            sender = message.from_user
            text = (message.text or message.caption or "").strip()
            media: tuple[InboundMedia, ...] = ()

            if message.voice or message.audio:
                transcript = await self._transcribe(message)
                if not transcript:
                    return  # no transcriber / silent / failed — nothing usable
                text = transcript
            elif message.photo or message.document or message.video:
                item = await self._download(message)
                if item is not None:
                    media = (item,)

            if not text and not media:
                return  # nothing we can hand to the model

            async def send(reply: Reply) -> None:
                # A media reply goes out as the real file by kind; on any send failure
                # fall back to the caption/path text so the user still gets something.
                if isinstance(reply, Media):
                    try:
                        await _tg_reply_media(message, reply)
                    except Exception:
                        await message.reply_text(as_text(reply))
                else:
                    # str passes through (Question → numbered text); split past Telegram's
                    # 4096-char cap so a long reply arrives as several messages, not an error.
                    for part in chunk_text(as_text(reply), TELEGRAM_LIMIT):
                        await message.reply_text(part)

            # Record where this turn came from, so scheduling tools (cron) can capture the chat
            # for later proactive delivery (the chat, not the sender).
            current_chat.set((self.NAME, str(message.chat_id)))
            name = sender.first_name or sender.username or str(sender.id)
            is_group = message.chat.type != "private"
            # Show "typing…" until the turn finishes (Telegram's action lasts ~5s, so refresh it).
            typing = asyncio.create_task(self._keep_typing(context.bot, message.chat_id))
            try:
                await self._dispatch(
                    str(sender.id), name, Inbound(text=text, media=media, is_group=is_group), send
                )
            finally:
                typing.cancel()

        # TEXT keeps slash-commands (/help, /reset, …) flowing (the handler dispatches them); the
        # rest add voice + media. Status updates (joins, pins) are excluded by these filters.
        app.add_handler(
            MessageHandler(
                filters.TEXT
                | filters.VOICE
                | filters.AUDIO
                | filters.PHOTO
                | filters.Document.ALL
                | filters.VIDEO,
                _on_message,
            )
        )
        return app

    async def _keep_typing(self, bot: Any, chat_id: int) -> None:
        """Hold the "typing…" chat action for the whole turn (best-effort; never breaks it)."""
        from telegram.constants import ChatAction

        try:
            while True:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)  # the indicator auto-expires after ~5s
        except asyncio.CancelledError:
            pass
        except Exception:  # presence is best-effort — a failure must not affect the reply
            pass

    async def _transcribe(self, message: Any) -> str:
        """Transcript of an inbound voice/audio note, or ``""`` (no transcriber / silent / error).

        The audio is downloaded to ``~/.gaia/cache/voice/`` and transcribed locally; a failure is
        logged and the message dropped — a bad voice note must never take the connector loop down.
        """
        if self._transcriber is None:
            return ""
        audio = message.voice or message.audio
        path = constants.CACHE_DIR / "voice" / f"tg-{message.message_id}.ogg"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tg_file = await audio.get_file()
            await tg_file.download_to_drive(custom_path=str(path))
            transcript = await self._transcriber.transcribe(path)
        except Exception as exc:
            log_error("voice_inbound", exc, channel="telegram")
            return ""
        # The prefix tells Gaia the modality, so it answers the spoken content naturally.
        return f"[voice message] {transcript}" if transcript else ""

    async def _download(self, message: Any) -> InboundMedia | None:
        """Download an inbound photo/document/video to ``UPLOADS_DIR`` (a sandbox root), else None.

        Saved under UPLOADS_DIR so a soul can copy it into its workspace and use the real file.
        Best-effort: a bad attachment is logged and dropped, never crashing the loop.
        """
        if message.photo:
            obj, kind, mime, fname = message.photo[-1], "image", "image/jpeg", None
        elif message.document:
            doc = message.document
            mime = doc.mime_type or "application/octet-stream"
            obj, kind, fname = doc, _media_kind(mime), doc.file_name
        elif message.video:
            obj, kind, mime, fname = message.video, "video", "video/mp4", None
        else:
            return None
        path = constants.UPLOADS_DIR / f"tg-{message.message_id}-{fname or kind}"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tg_file = await obj.get_file()
            await tg_file.download_to_drive(custom_path=str(path))
        except Exception as exc:
            log_error("inbound_drop", exc, channel="telegram", kind=kind)
            return None
        return InboundMedia(path=path, mime=mime, kind=kind)

    def run(self) -> None:
        """Start long-polling on its own event loop. Blocks. Use standalone."""
        self.build_application().run_polling()  # type: ignore[attr-defined]

    async def start(self) -> None:
        """Start long-polling inside the *caller's* event loop and block on it.

        Unlike :meth:`run` (which owns the loop via ``run_polling``), this drives the
        python-telegram-bot lifecycle by hand so it can be ``asyncio.gather``-ed with
        other async connectors. Cancelling the task tears the application down.
        """
        app: Any = self.build_application()
        await app.initialize()
        await self._register_commands(app)
        await app.start()
        await app.updater.start_polling()
        self._app = app  # expose the live bot for proactive send_to
        try:
            await asyncio.Event().wait()  # block until cancelled
        finally:
            self._app = None
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    async def send_to(self, chat: str, reply: Reply) -> None:
        """Proactively send ``reply`` to ``chat`` (a telegram chat id) — used by cron.

        Only works while :meth:`start` is polling (the daemon); raises otherwise so the
        caller logs a clear delivery failure instead of silently dropping the message.
        """
        if self._app is None:
            raise RuntimeError("telegram connector is not running")
        if isinstance(reply, Media):
            await _tg_send_media(self._app.bot, chat, reply)
        else:
            for part in chunk_text(as_text(reply), TELEGRAM_LIMIT):
                await self._app.bot.send_message(chat_id=chat, text=part)

    async def _register_commands(self, app: Any) -> None:
        """Register the in-chat commands with Telegram (setMyCommands) so typing '/' shows a menu.

        Only conforming names (``[a-z0-9_]{1,32}``) are sent; summaries are capped at 256 chars.
        """
        if not self._commands:
            return
        from telegram import BotCommand

        menu = [
            BotCommand(name, summary[:256])
            for name, summary in self._commands
            if _TG_COMMAND_NAME.fullmatch(name)
        ]
        if menu:
            await app.bot.set_my_commands(menu)

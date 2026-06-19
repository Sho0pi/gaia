"""Telegram adapter built on python-telegram-bot (async).

Deferred import keeps the connector module importable without the telegram dep
installed, so unit tests can exercise wiring without a live bot.
"""

from __future__ import annotations

from typing import Any

from gaia.connectors.base import Dispatch, Inbound, Media, Reply, as_text, current_chat


class TelegramConnector:
    """Bridges Telegram messages to the dispatcher (per-sender identity → user)."""

    #: Connector id used in cron job channel fields / the daemon's connector registry.
    NAME = "telegram"

    def __init__(self, token: str, dispatch: Dispatch) -> None:
        self._token = token
        self._dispatch = dispatch  # channel-bound: (sender_id, name, text, send)
        self._app: Any = None  # the live Application while start() runs (for send_to)

    def build_application(self) -> object:
        """Create a python-telegram-bot Application wired to the dispatcher."""
        from telegram import Update
        from telegram.ext import Application, MessageHandler, filters

        app = Application.builder().token(self._token).build()

        async def _on_message(update: Update, _context: object) -> None:
            message = update.message
            if message and message.text and message.from_user:
                sender = message.from_user

                async def send(reply: Reply) -> None:
                    # Telegram media replies (reply_photo) are a follow-up; for now a
                    # Media reply degrades to its caption/path text.
                    await message.reply_text(as_text(reply))

                # Record where this turn came from, so scheduling tools (cron) can
                # capture the chat for later proactive delivery (the chat, not the sender).
                current_chat.set((self.NAME, str(message.chat_id)))
                name = sender.first_name or sender.username or str(sender.id)
                await self._dispatch(str(sender.id), name, Inbound(text=message.text or ""), send)

        # filters.TEXT keeps slash-commands (/help, /reset, …) flowing to the handler,
        # which dispatches them itself; ~COMMAND would swallow them before Gaia sees them.
        app.add_handler(MessageHandler(filters.TEXT, _on_message))
        return app

    def run(self) -> None:
        """Start long-polling on its own event loop. Blocks. Use standalone."""
        self.build_application().run_polling()  # type: ignore[attr-defined]

    async def start(self) -> None:
        """Start long-polling inside the *caller's* event loop and block on it.

        Unlike :meth:`run` (which owns the loop via ``run_polling``), this drives the
        python-telegram-bot lifecycle by hand so it can be ``asyncio.gather``-ed with
        other async connectors. Cancelling the task tears the application down.
        """
        import asyncio

        app: Any = self.build_application()
        await app.initialize()
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
            with reply.path.open("rb") as fh:
                await self._app.bot.send_photo(chat_id=chat, photo=fh, caption=reply.caption)
        else:
            await self._app.bot.send_message(chat_id=chat, text=reply)

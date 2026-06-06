"""Telegram adapter built on python-telegram-bot (async).

Deferred import keeps the connector module importable without the telegram dep
installed, so unit tests can exercise wiring without a live bot.
"""

from __future__ import annotations

from godpy.connectors.base import Handler


class TelegramConnector:
    """Bridges Telegram messages to a God handler coroutine."""

    def __init__(self, token: str, handler: Handler) -> None:
        self._token = token
        self._handler = handler

    def build_application(self) -> object:
        """Create a python-telegram-bot Application wired to the handler."""
        from telegram import Update
        from telegram.ext import Application, MessageHandler, filters

        app = Application.builder().token(self._token).build()

        async def _on_message(update: Update, _context: object) -> None:
            if update.message and update.message.text:
                message = update.message

                async def send(reply: str) -> None:
                    await message.reply_text(reply)

                await self._handler(update.message.text, send)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
        return app

    def run(self) -> None:
        """Start long-polling. Blocks."""
        self.build_application().run_polling()  # type: ignore[attr-defined]

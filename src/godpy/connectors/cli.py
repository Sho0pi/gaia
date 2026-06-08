"""Local CLI connector with a Textual chat TUI.

A full-screen terminal chat: you type, God answers as markdown bubbles, with a
spinner while it thinks. Like every other connector it's a dumb pipe over the
shared :data:`~godpy.connectors.base.Handler` contract — each reply the handler
streams through ``send`` becomes a bubble in the log.

Textual is imported lazily inside :meth:`CLIConnector.build_app` (per the heavy-dep
convention) so ``godpy.connectors`` stays importable without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from godpy import constants
from godpy.connectors.base import Handler

if TYPE_CHECKING:  # pragma: no cover - typing only
    from textual.app import App


class CLIConnector:
    """Bridges a terminal chat UI to a God handler coroutine."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler

    def build_app(self) -> App[None]:
        """Build the Textual chat app wired to the handler. Imports Textual lazily."""
        from textual.app import App, ComposeResult
        from textual.binding import BindingType
        from textual.containers import VerticalScroll
        from textual.widgets import (
            Footer,
            Header,
            Input,
            LoadingIndicator,
            Markdown,
            Static,
        )

        handler = self._handler

        class ChatApp(App):  # type: ignore[type-arg]
            TITLE = constants.APP_NAME
            CSS = """
            #log { padding: 1 2; }
            .bubble { width: auto; max-width: 80%; padding: 0 1; margin-top: 1; }
            .user { margin-left: 10; background: $primary; color: $text; }
            .god { margin-right: 10; background: $panel; }
            #prompt { dock: bottom; margin: 0 1 1 1; }
            """
            BINDINGS: ClassVar[list[BindingType]] = [
                ("ctrl+l", "clear_log", "Clear"),
                ("ctrl+c", "quit", "Quit"),
            ]

            def compose(self) -> ComposeResult:
                yield Header()
                yield VerticalScroll(id="log")
                yield Input(placeholder="type a message", id="prompt")
                yield Footer()

            def on_mount(self) -> None:
                self.query_one("#prompt", Input).focus()

            async def on_input_submitted(self, event: Input.Submitted) -> None:
                text = event.value.strip()
                if not text:
                    return
                event.input.value = ""
                log = self.query_one("#log", VerticalScroll)
                await log.mount(Static(text, classes="user bubble"))
                loading = LoadingIndicator(classes="loading")
                await log.mount(loading)
                log.scroll_end(animate=False)
                self.run_worker(self._respond(text, loading))

            async def _respond(self, text: str, loading: LoadingIndicator) -> None:
                log = self.query_one("#log", VerticalScroll)

                async def send(reply: str) -> None:
                    await log.mount(Markdown(reply, classes="god bubble"))
                    log.scroll_end(animate=False)

                try:
                    await handler(text, send)
                finally:
                    await loading.remove()

            def action_clear_log(self) -> None:
                self.query_one("#log", VerticalScroll).remove_children()

        return ChatApp()

    def run(self) -> None:
        """Launch the TUI. Blocks until the user quits."""
        self.build_app().run()

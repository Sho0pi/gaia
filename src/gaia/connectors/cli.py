"""Local CLI connector with a Textual chat TUI.

A full-screen terminal chat: you type, Gaia answers as markdown bubbles, with a
spinner while it thinks. Like every other connector it's a dumb pipe over the
shared :data:`~gaia.connectors.base.Handler` contract — each reply the handler
streams through ``send`` becomes a bubble in the log.

Textual is imported lazily inside :meth:`CLIConnector.build_app` (per the heavy-dep
convention) so ``gaia.connectors`` stays importable without it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

from gaia import constants
from gaia.connectors.base import Dispatch, Reply, as_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from textual.app import App


class CLIConnector:
    """Bridges a terminal chat UI to the dispatcher as the local operator.

    The local cli sender is the fixed identity ``local`` (resolved by the dispatcher to
    the trusted ``admin`` role via the cli connector's ``default_role``).
    """

    #: Connector id used in the dispatcher / connector registry.
    NAME = "cli"
    #: The single local sender id; the operator who owns the terminal.
    SENDER = "local"

    def __init__(self, dispatch: Dispatch) -> None:
        self._dispatch = dispatch

    def build_app(self) -> App[None]:
        """Build the Textual chat app wired to the dispatcher. Imports Textual lazily."""
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

        dispatch = self._dispatch
        sender = self.SENDER

        class ChatApp(App):  # type: ignore[type-arg]
            TITLE = constants.APP_NAME
            CSS = """
            #log { padding: 1 2; }
            .bubble { width: auto; max-width: 80%; padding: 0 1; margin-top: 1; }
            .user { margin-left: 10; background: $primary; color: $text; }
            .gaia { margin-right: 10; background: $panel; }
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

                async def send(reply: Reply) -> None:
                    # The TUI shows media as its caption/path for now (inline image
                    # rendering is a follow-up).
                    await log.mount(Markdown(as_text(reply), classes="gaia bubble"))
                    log.scroll_end(animate=False)

                try:
                    await dispatch(sender, "operator", text, send)
                finally:
                    await loading.remove()

            def action_clear_log(self) -> None:
                self.query_one("#log", VerticalScroll).remove_children()

        return ChatApp()

    async def run_async(self) -> None:
        """Run the TUI on the *current* event loop until the user quits.

        The caller owns the loop, so anything that must outlive the app but die with
        the loop (Gaia's async resources) can be closed right after this returns —
        typically ``async with gaia: await connector.run_async()``.
        """
        await self.build_app().run_async()

    def run(self) -> None:
        """Launch the TUI on its own fresh loop. Blocks until the user quits."""
        asyncio.run(self.run_async())

"""Local inline CLI chat connector.

The default terminal chat is intentionally *not* a full-screen TUI. It behaves like a
normal CLI REPL: read one line, send it through Gaia's shared dispatcher, render each
reply inline, repeat. Rich handles pleasant output; prompt_toolkit handles the input
prompt and terminal history.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, cast

from gaia import constants
from gaia.cli._console import console
from gaia.connectors.base import Dispatch, Inbound, Reply, as_text

InputFunc = Callable[[str], Awaitable[str]]


class CLIConnector:
    """Bridges an inline terminal chat loop to the dispatcher as the local operator.

    The local cli sender is the fixed identity ``local`` (resolved by the dispatcher to
    the trusted ``admin`` role via the cli connector's ``default_role``).
    """

    #: Connector id used in the dispatcher / connector registry.
    NAME = "cli"
    #: The single local sender id; the operator who owns the terminal.
    SENDER = "local"
    #: Commands that end the local chat without hitting the model.
    EXIT_COMMANDS: ClassVar[set[str]] = {"/exit", "/quit", "exit", "quit"}

    def __init__(
        self,
        dispatch: Dispatch,
        *,
        input_func: InputFunc | None = None,
    ) -> None:
        self._dispatch = dispatch
        self._input = input_func
        self._session: Any | None = None

    async def _prompt(self, prompt: str) -> str:
        if self._input is not None:
            return await self._input(prompt)

        # Heavy import stays lazy so package import/tests do not require the UI stack.
        from prompt_toolkit import PromptSession

        if self._session is None:
            self._session = PromptSession()
        return cast(str, await self._session.prompt_async(prompt))

    async def run_async(self) -> None:
        """Run an inline chat loop on the current event loop until the user quits."""
        out = console()
        out.print(f"[bold]{constants.APP_NAME}[/] chat — /exit to quit")

        while True:
            try:
                raw = await self._prompt("You > ")
            except (EOFError, KeyboardInterrupt):
                out.print("\n[dim]bye[/]")
                return

            text = raw.strip()
            if not text:
                continue
            if text.lower() in self.EXIT_COMMANDS:
                out.print("[dim]bye[/]")
                return

            with out.status("[dim]Gaia thinking...[/]", spinner="dots"):
                await self._dispatch_one(text, out.print)

    async def _dispatch_one(self, text: str, print_reply: Callable[[object], None]) -> None:
        async def send(reply: Reply) -> None:
            print_reply("")
            print_reply(f"[bold magenta]Gaia[/] [dim]>[/] {as_text(reply)}")

        await self._dispatch(self.SENDER, "operator", Inbound(text=text), send)

    def run(self) -> None:
        """Launch the inline chat on its own fresh loop. Blocks until the user quits."""
        asyncio.run(self.run_async())

"""Unit tests for the Textual CLI connector.

The app is driven headlessly via Textual's ``run_test`` Pilot with a fake handler
(no Gaia/ADK), so we verify the wiring — inbound text reaches the handler and each
streamed reply becomes a bubble — without a model backend.
"""

from __future__ import annotations

from gaia.connectors.base import Send
from gaia.connectors.cli import CLIConnector


async def _type(pilot: object, app: object, text: str) -> None:
    """Type ``text`` into the prompt, submit, and let the worker finish."""
    from textual.widgets import Input

    app.query_one("#prompt", Input).value = text  # type: ignore[attr-defined]
    await pilot.press("enter")  # type: ignore[attr-defined]
    await app.workers.wait_for_complete()  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]


async def test_streams_replies_into_chat() -> None:
    from textual.widgets import Input, Markdown

    seen: list[str] = []

    async def handler(text: str, send: Send) -> None:
        seen.append(text)
        await send("**a**")
        await send("b")

    app = CLIConnector(handler).build_app()
    async with app.run_test() as pilot:
        await _type(pilot, app, "hi")

        assert seen == ["hi"]  # inbound text reached the handler
        assert len(app.query("Static.user")) == 1  # the user's bubble
        assert len(app.query(Markdown)) == 2  # one gaia bubble per streamed reply part
        assert app.query_one("#prompt", Input).value == ""  # input cleared after submit


async def test_empty_input_ignored() -> None:
    from textual.widgets import Markdown

    called = False

    async def handler(_text: str, _send: Send) -> None:
        nonlocal called
        called = True

    app = CLIConnector(handler).build_app()
    async with app.run_test() as pilot:
        await _type(pilot, app, "   ")

        assert called is False
        assert len(app.query(Markdown)) == 0
        assert len(app.query("Static.user")) == 0

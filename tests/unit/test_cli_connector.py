"""Unit tests for the inline CLI connector.

The connector runs as a normal terminal REPL (no Textual/full-screen app). Tests inject
input lines and a fake dispatch so no Gaia/ADK/model backend is needed.
"""

from __future__ import annotations

from gaia.connectors.base import Inbound, Send
from gaia.connectors.cli import CLIConnector


async def test_streams_replies_into_chat(capsys) -> None:  # type: ignore[no-untyped-def]
    seen: list[tuple[str, str]] = []  # (sender_id, text)
    inputs = iter(["hi", "/exit"])

    async def prompt(_prompt: str) -> str:
        return next(inputs)

    async def dispatch(sender_id: str, _name: str, inbound: Inbound, send: Send) -> None:
        seen.append((sender_id, inbound.text))
        await send("**a**")
        await send("b")

    await CLIConnector(dispatch, input_func=prompt).run_async()

    assert seen == [("local", "hi")]
    output = capsys.readouterr().out
    assert "Gaia > **a**" in output
    assert "Gaia > b" in output
    assert "bye" in output


async def test_empty_input_ignored() -> None:
    called = False
    inputs = iter(["   ", "/exit"])

    async def prompt(_prompt: str) -> str:
        return next(inputs)

    async def dispatch(_sender_id: str, _name: str, _inbound: Inbound, _send: Send) -> None:
        nonlocal called
        called = True

    await CLIConnector(dispatch, input_func=prompt).run_async()

    assert called is False


async def test_eof_exits_chat() -> None:
    called = False

    async def prompt(_prompt: str) -> str:
        raise EOFError

    async def dispatch(_sender_id: str, _name: str, _inbound: Inbound, _send: Send) -> None:
        nonlocal called
        called = True

    await CLIConnector(dispatch, input_func=prompt).run_async()

    assert called is False


# Shutdown ordering (Gaia closed on the same loop right after the chat exits) is
# covered in test_connector_launch.py / test_gaia_agent.py — the connector itself no
# longer carries a shutdown hook.

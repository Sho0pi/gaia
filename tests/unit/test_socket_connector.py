from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from gaia.connectors.base import Inbound, Send
from gaia.connectors.socket import DaemonNotRunningError, SocketChatClient, SocketConnector


async def test_socket_connector_dispatches_replies(tmp_path: Path) -> None:
    seen: list[tuple[str, str, str]] = []

    async def dispatch(sender: str, name: str, inbound: Inbound, send: Send) -> None:
        seen.append((sender, name, inbound.text))
        await send("one")
        await send("two")

    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        socket_path = Path(tmp) / "gaia.sock"
        connector = SocketConnector(socket_path, dispatch)
        task = asyncio.create_task(connector.start())
        try:
            for _ in range(100):
                if socket_path.exists():
                    break
                await asyncio.sleep(0.01)
            replies: list[str] = []

            async def send(reply: object) -> None:
                replies.append(str(reply))

            await SocketChatClient(socket_path).dispatch(
                "ignored", "ignored", Inbound(text="hi"), send
            )

            assert seen == [("local", "operator", "hi")]
            assert replies == ["one", "two"]
            for _ in range(100):
                if connector.clients == 0:
                    break
                await asyncio.sleep(0.01)
            assert connector.clients == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert not socket_path.exists()


async def test_socket_client_missing_daemon_fails(tmp_path: Path) -> None:
    with pytest.raises(DaemonNotRunningError, match="gaia start"):
        await SocketChatClient(tmp_path / "missing.sock").ensure_available()

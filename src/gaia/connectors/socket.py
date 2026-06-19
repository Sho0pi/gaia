"""Unix-socket gateway used by local CLI clients to attach to the daemon."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
from pathlib import Path

from gaia.connectors.base import Dispatch, Inbound, Media, Reply
from gaia.connectors.socket_protocol import (
    ProtocolError,
    decode_frame,
    done_frame,
    encode_frame,
    error_frame,
    hello_frame,
    media_frame,
    message_frame,
    reply_frame,
    require_text,
)

logger = logging.getLogger(__name__)


class DaemonNotRunningError(ConnectionError):
    """Raised when a CLI client cannot connect to the daemon socket."""


class SocketConnector:
    """Daemon-side local gateway over a Unix domain socket."""

    NAME = "socket"

    def __init__(self, path: Path, dispatch: Dispatch) -> None:
        self.path = path
        self._dispatch = dispatch
        self._clients = 0

    @property
    def clients(self) -> int:
        return self._clients

    async def start(self) -> None:
        """Serve local clients until cancelled, then close and unlink the socket."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        bind_path = self._bind_path()
        self.path.unlink(missing_ok=True)
        bind_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._handle, path=bind_path)
        os.chmod(bind_path, 0o600)
        if bind_path != self.path:
            self.path.symlink_to(bind_path)
        logger.info("daemon socket listening at %s", self.path)
        try:
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            raise
        finally:
            server.close()
            await server.wait_closed()
            self.path.unlink(missing_ok=True)
            if bind_path != self.path:
                bind_path.unlink(missing_ok=True)

    def _bind_path(self) -> Path:
        """Return a bindable path, avoiding macOS/Linux AF_UNIX length limits."""
        if len(str(self.path)) < 100:
            return self.path
        digest = hashlib.sha256(str(self.path).encode()).hexdigest()[:16]
        return Path("/tmp") / f"gaia-{digest}.sock"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients += 1
        try:
            writer.write(encode_frame(hello_frame()))
            await writer.drain()
            while line := await reader.readline():
                try:
                    frame = decode_frame(line)
                    if frame["type"] != "message":
                        raise ProtocolError("expected message frame")
                    text = require_text(frame)
                    await self._dispatch_message(text, writer)
                except ProtocolError as exc:
                    writer.write(encode_frame(error_frame(str(exc))))
                    await writer.drain()
        finally:
            self._clients -= 1
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch_message(self, text: str, writer: asyncio.StreamWriter) -> None:
        async def send(reply: Reply) -> None:
            if isinstance(reply, Media):
                writer.write(encode_frame(media_frame(str(reply.path), reply.caption, reply.kind)))
            else:
                writer.write(encode_frame(reply_frame(reply)))
            await writer.drain()

        try:
            await self._dispatch("local", "operator", Inbound(text=text), send)
        except Exception as exc:
            logger.exception("daemon socket dispatch failed")
            writer.write(encode_frame(error_frame(str(exc))))
            await writer.drain()
        finally:
            writer.write(encode_frame(done_frame()))
            await writer.drain()


class SocketChatClient:
    """Client-side adapter exposing daemon socket messages as a Dispatch callable."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def ensure_available(self) -> None:
        reader, writer = await self._connect()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        del reader

    async def dispatch(
        self, _sender: str, _name: str, inbound: Inbound, send: DispatchReply
    ) -> None:
        reader, writer = await self._connect()
        try:
            writer.write(encode_frame(message_frame(inbound.text)))
            await writer.drain()
            while line := await reader.readline():
                frame = decode_frame(line)
                frame_type = frame["type"]
                if frame_type == "reply":
                    await send(require_text(frame))
                elif frame_type == "media":
                    path = Path(require_text(frame, "path"))
                    await send(Media(path, require_text(frame, "caption")))
                elif frame_type == "done":
                    return
                elif frame_type == "error":
                    raise RuntimeError(require_text(frame, "message"))
                elif frame_type == "hello":
                    continue
                else:  # pragma: no cover - decode_frame rejects unknown types
                    raise ProtocolError(f"unexpected frame: {frame_type}")
            raise DaemonNotRunningError("daemon socket closed")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            reader, writer = await asyncio.open_unix_connection(self.path)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            raise DaemonNotRunningError("daemon not running — run gaia start") from exc
        line = await reader.readline()
        if not line:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            raise DaemonNotRunningError("daemon socket closed")
        frame = decode_frame(line)
        if frame["type"] != "hello":
            raise ProtocolError("daemon did not send hello")
        return reader, writer


from gaia.connectors.base import Send as DispatchReply  # noqa: E402  (keeps aliases local)

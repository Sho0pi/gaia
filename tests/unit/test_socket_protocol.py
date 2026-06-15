from __future__ import annotations

import pytest

from gaia.connectors.socket_protocol import (
    ProtocolError,
    decode_frame,
    done_frame,
    encode_frame,
    hello_frame,
    message_frame,
    require_text,
)


def test_round_trips_frame() -> None:
    assert decode_frame(encode_frame(message_frame("hi"))) == {"type": "message", "text": "hi"}
    assert decode_frame(encode_frame(done_frame())) == {"type": "done"}


def test_hello_is_versioned() -> None:
    assert hello_frame() == {"type": "hello", "version": 1}


@pytest.mark.parametrize("line", [b"not-json\n", b"[]\n", b'{"type":"wat"}\n'])
def test_rejects_bad_frames(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode_frame(line)


def test_require_text_rejects_missing_text() -> None:
    with pytest.raises(ProtocolError):
        require_text({"type": "message"})

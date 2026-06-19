"""Line-delimited JSON protocol for local CLI clients talking to the daemon."""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

PROTOCOL_VERSION = 1

FrameType = Literal["hello", "message", "reply", "media", "done", "error"]


class Frame(TypedDict, total=False):
    type: FrameType
    version: int
    text: str
    path: str
    caption: str
    kind: str
    message: str


class ProtocolError(ValueError):
    """Raised when a socket frame is not valid Gaia daemon protocol."""


def encode_frame(frame: Frame) -> bytes:
    """Encode one protocol frame as compact UTF-8 JSON plus newline."""
    if "type" not in frame:
        raise ProtocolError("frame missing type")
    return (json.dumps(frame, separators=(",", ":")) + "\n").encode()


def decode_frame(line: bytes) -> Frame:
    """Decode and lightly validate one newline-delimited JSON frame."""
    try:
        raw = json.loads(line.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid json frame") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        raise ProtocolError("frame missing type")
    frame_type = raw["type"]
    if frame_type not in {"hello", "message", "reply", "media", "done", "error"}:
        raise ProtocolError(f"unknown frame type: {frame_type}")
    return raw  # type: ignore[return-value]


def hello_frame() -> Frame:
    return {"type": "hello", "version": PROTOCOL_VERSION}


def message_frame(text: str) -> Frame:
    return {"type": "message", "text": text}


def reply_frame(text: str) -> Frame:
    return {"type": "reply", "text": text}


def media_frame(path: str, caption: str, kind: str = "") -> Frame:
    return {"type": "media", "path": path, "caption": caption, "kind": kind}


def done_frame() -> Frame:
    return {"type": "done"}


def error_frame(message: str) -> Frame:
    return {"type": "error", "message": message}


def require_text(frame: Frame, key: str = "text") -> str:
    value: Any = frame.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f"frame missing string {key}")
    return value

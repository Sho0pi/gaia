"""Unit tests for GaiaHandler — the text -> ADK -> reply glue.

The ADK ``Runner`` is replaced with a fake whose ``run_async`` yields canned
events, so the streaming behaviour (one ``send`` per text part of the final
response) is verified without a model backend. ``google.genai.types`` is a real
dep (via google-adk) and constructs offline, so no key is needed.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.core.handler import GaiaHandler


def _event(*texts: str, final: bool = True) -> SimpleNamespace:
    """Fake ADK event carrying one Part per text (empty string -> a text-less part)."""
    parts = [SimpleNamespace(text=t or None) for t in texts]
    return SimpleNamespace(
        content=SimpleNamespace(parts=parts),
        is_final_response=lambda: final,
    )


class _FakeRunner:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events

    async def run_async(self, **_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        for event in self._events:
            yield event


async def _collect(handler: GaiaHandler, text: str) -> list[str]:
    sent: list[str] = []

    async def send(reply: str) -> None:
        sent.append(reply)

    await handler(text, send)
    return sent


async def test_streams_each_text_part_of_final_response() -> None:
    # gaia only needs memory_service here: None short-circuits auto-ingest.
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_event("hello", "", "world")])

    sent = await _collect(handler, "hi")

    # The empty (text-less) part is skipped; the rest stream in order.
    assert sent == ["hello", "world"]


async def test_ignores_non_final_events() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_event("interim", final=False), _event("done")])

    assert await _collect(handler, "hi") == ["done"]


class _ExplodingRunner:
    """A runner that fails if the model is ever invoked (commands must not reach it)."""

    async def run_async(self, **_kwargs: Any) -> Any:
        raise AssertionError("run_async must not be called for a command")
        yield  # pragma: no cover - makes this an async generator


async def test_command_runs_instead_of_model() -> None:
    from gaia.config import GaiaConfig

    handler = GaiaHandler(SimpleNamespace(config=GaiaConfig(), memory_service=None))
    handler._runner = _ExplodingRunner()

    sent = await _collect(handler, "/help")

    assert sent and sent[0].startswith("Commands:")  # handled out-of-band, model untouched


async def test_unknown_command_replies_hint() -> None:
    from gaia.config import GaiaConfig

    handler = GaiaHandler(SimpleNamespace(config=GaiaConfig(), memory_service=None))
    handler._runner = _ExplodingRunner()

    assert await _collect(handler, "/nope") == ["Unknown command '/nope'. Try /help."]


async def test_plain_text_still_reaches_the_model() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_event("answer")])

    assert await _collect(handler, "not a command") == ["answer"]


class _BoomRunner:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def run_async(self, **_kwargs: Any) -> Any:
        raise self._exc
        yield  # pragma: no cover - makes this an async generator


async def test_model_error_yields_friendly_message_not_traceback() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _BoomRunner(RuntimeError("429 RESOURCE_EXHAUSTED quota"))

    sent = await _collect(handler, "hi")  # must not raise

    assert len(sent) == 1
    assert "rate-limited" in sent[0]


async def test_generic_error_yields_generic_message() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _BoomRunner(ValueError("boom"))

    sent = await _collect(handler, "hi")

    assert sent == ["Sorry — something went wrong handling that. Please try again."]


def _gaia(*, batch_size: int = 2, interval: int = 3600, auto_ingest: bool = True) -> Any:
    """Fake Gaia whose memory service records each add_events_to_memory call."""
    calls: list[dict[str, Any]] = []

    async def add_events_to_memory(**kwargs: Any) -> None:
        calls.append(kwargs)

    service = SimpleNamespace(calls=calls, add_events_to_memory=add_events_to_memory)
    memory = SimpleNamespace(
        auto_ingest=auto_ingest,
        ingest_batch_size=batch_size,
        ingest_interval_seconds=interval,
    )
    return SimpleNamespace(memory_service=service, config=SimpleNamespace(memory=memory))


async def test_buffers_until_batch_size_then_flushes_once() -> None:
    gaia = _gaia(batch_size=2)
    handler = GaiaHandler(gaia)
    handler._runner = _FakeRunner([_event("ok")])  # one event per turn

    await _collect(handler, "msg 1")
    assert gaia.memory_service.calls == []  # 1 < 2 buffered, nothing ingested yet

    await _collect(handler, "msg 2")
    assert len(gaia.memory_service.calls) == 1  # threshold reached → single flush
    assert len(gaia.memory_service.calls[0]["events"]) == 2  # both turns in one batch
    assert handler._buffer == []  # buffer drained


async def test_flush_drains_remaining_buffer() -> None:
    gaia = _gaia(batch_size=100)  # never auto-flushes
    handler = GaiaHandler(gaia)
    handler._runner = _FakeRunner([_event("ok")])

    await _collect(handler, "lonely message")
    assert gaia.memory_service.calls == []  # below threshold

    await handler.flush()  # shutdown-style drain
    assert len(gaia.memory_service.calls) == 1
    assert len(gaia.memory_service.calls[0]["events"]) == 1
    assert handler._buffer == []


async def test_auto_ingest_off_never_buffers() -> None:
    gaia = _gaia(auto_ingest=False)
    handler = GaiaHandler(gaia)
    handler._runner = _FakeRunner([_event("ok")])

    await _collect(handler, "msg")

    assert handler._buffer == []
    await handler.flush()
    assert gaia.memory_service.calls == []


def _screenshot_event(path: str, status: str = "success") -> SimpleNamespace:
    """A fake event whose tool response is a browser_screenshot result."""
    resp = SimpleNamespace(name="browser_screenshot", response={"status": status, "path": path})
    return SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(text=None)]),
        is_final_response=lambda: False,
        get_function_responses=lambda: [resp],
    )


async def _collect_replies(handler: GaiaHandler, text: str) -> list[Any]:
    """Like _collect but keeps Reply objects (str or Media), not just text."""
    sent: list[Any] = []

    async def send(reply: Any) -> None:
        sent.append(reply)

    await handler(text, send)
    return sent


async def test_screenshot_result_is_sent_as_media() -> None:
    from gaia.connectors.base import Media

    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_screenshot_event("/tmp/shot.png"), _event("here it is")])

    sent = await _collect_replies(handler, "screenshot google")

    assert "here it is" in sent  # the text reply still streams
    media = [r for r in sent if isinstance(r, Media)]
    assert len(media) == 1
    assert str(media[0].path) == "/tmp/shot.png" and media[0].caption == "screenshot"


async def test_failed_screenshot_is_not_sent() -> None:
    from gaia.connectors.base import Media

    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_screenshot_event("/tmp/x.png", status="error"), _event("done")])

    sent = await _collect_replies(handler, "shot")

    assert not [r for r in sent if isinstance(r, Media)]


def _mcp_screenshot_event(content: list[dict[str, Any]], is_error: bool = False) -> SimpleNamespace:
    """A fake event whose tool response is a playwright-mcp browser_take_screenshot result."""
    resp = SimpleNamespace(
        name="browser_take_screenshot", response={"content": content, "isError": is_error}
    )
    return SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(text=None)]),
        is_final_response=lambda: False,
        get_function_responses=lambda: [resp],
    )


async def test_mcp_screenshot_path_in_text_is_sent_as_media(tmp_path: Path) -> None:
    from gaia.connectors.base import Media

    shot = tmp_path / "page-123.png"
    shot.write_bytes(b"\x89PNG fake")
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner(
        [
            _mcp_screenshot_event([{"type": "text", "text": f"Saved screenshot as {shot}"}]),
            _event("done"),
        ]
    )

    sent = await _collect_replies(handler, "shot")

    media = [r for r in sent if isinstance(r, Media)]
    assert len(media) == 1
    assert media[0].path == shot


async def test_mcp_screenshot_markdown_link_resolved_against_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # playwright-mcp's real shape: a markdown link to a cwd-relative file (it ignores
    # --output-dir for the screenshot name), e.g. "[Screenshot of viewport](./flow.png)".
    # We pin the server cwd to the workspace, so the basename resolves there.
    from gaia.connectors.base import Media

    monkeypatch.setattr("gaia.mcp.browser_output_dir", lambda: tmp_path)
    shot = tmp_path / "flow-with-grace.png"
    shot.write_bytes(b"\x89PNG saved")
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner(
        [
            _mcp_screenshot_event(
                [
                    {
                        "type": "text",
                        "text": "### Result\n- [Screenshot of viewport](./flow-with-grace.png)",
                    }
                ]
            ),
            _event("done"),
        ]
    )

    sent = await _collect_replies(handler, "shot")

    media = [r for r in sent if isinstance(r, Media)]
    assert len(media) == 1
    assert media[0].path == shot


async def test_mcp_screenshot_inline_image_is_written_and_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gaia.connectors.base import Media

    monkeypatch.setattr("gaia.mcp.browser_output_dir", lambda: tmp_path)
    data = base64.b64encode(b"\x89PNG inline").decode()
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner(
        [
            _mcp_screenshot_event([{"type": "image", "mimeType": "image/png", "data": data}]),
            _event("done"),
        ]
    )

    sent = await _collect_replies(handler, "shot")

    media = [r for r in sent if isinstance(r, Media)]
    assert len(media) == 1
    assert media[0].path.parent == tmp_path
    assert media[0].path.read_bytes() == b"\x89PNG inline"


async def test_mcp_screenshot_error_is_not_sent() -> None:
    from gaia.connectors.base import Media

    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner(
        [_mcp_screenshot_event([{"type": "text", "text": "boom"}], is_error=True), _event("done")]
    )

    sent = await _collect_replies(handler, "shot")

    assert not [r for r in sent if isinstance(r, Media)]

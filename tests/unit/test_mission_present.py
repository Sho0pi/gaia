"""present_result: Gaia's present-turn delivers its replies (text + screenshots) to chat."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from _fakes import FakeSender as _FakeSender
from gaia.connectors.base import Media
from gaia.missions import Task
from gaia.missions.present import present_result
from gaia.souls.run import SoulRun
from gaia.users import UserStore


def _gaia(tmp_path: Path, connectors: dict[str, Any]) -> Any:
    return SimpleNamespace(
        connectors=connectors,
        users=UserStore(tmp_path / "u.json"),
        config=SimpleNamespace(cron=SimpleNamespace(deliver=SimpleNamespace(channel="", chat=""))),
    )


def _fake_handler(monkeypatch: pytest.MonkeyPatch, replies: list[Any]) -> None:
    """Replace build_handler so the present turn 'emits' the scripted replies via send."""

    def build(gaia: Any, **kw: Any) -> Any:
        async def handler(text: str, send: Any) -> None:
            for r in replies:
                await send(r)

        return handler

    monkeypatch.setattr("gaia.core.handler.build_handler", build)


async def test_present_delivers_gaia_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"PNG")
    _fake_handler(monkeypatch, ["Here's your site.", Media(path=shot)])
    wa = _FakeSender()
    gaia = _gaia(tmp_path, {"whatsapp": wa})
    task = Task(title="site", notify_channel="whatsapp", notify_chat="972@x")

    await present_result(gaia, task, SoulRun(True, "s", "S", False, summary="built", files=[]))

    replies = [reply for _chat, reply in wa.sent]
    assert "Here's your site." in replies
    assert any(isinstance(r, Media) for r in replies)  # Gaia's screenshot delivered


async def test_no_connector_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_handler(monkeypatch, ["x"])
    gaia = _gaia(tmp_path, {})  # nothing live
    task = Task(title="site", notify_channel="whatsapp", notify_chat="972@x")

    await present_result(gaia, task, SoulRun(True, "s", "S", False, summary="b"))  # no raise

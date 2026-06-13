"""present_result: Gaia present-turn delivers its replies + a render fallback for sites."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import gaia.missions.present as present_mod
from gaia.connectors.base import Media
from gaia.missions import Task
from gaia.missions.present import present_result
from gaia.souls.run import SoulRun
from gaia.users import UserStore


class _FakeSender:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send_to(self, chat: str, reply: Any) -> None:
        self.sent.append(reply)


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

    assert "Here's your site." in wa.sent
    assert any(isinstance(r, Media) for r in wa.sent)  # Gaia's screenshot delivered


async def test_render_fallback_when_turn_shows_no_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.html").write_text("<h1>Site</h1>")
    _fake_handler(monkeypatch, ["A site, but I forgot to screenshot it."])  # text only, no Media

    async def fake_render(html: Path, out_png: Path) -> Path:
        out_png.write_bytes(b"PNG")  # noqa: ASYNC240 - test stub
        return out_png

    monkeypatch.setattr(present_mod, "render_html_to_png", fake_render, raising=False)
    monkeypatch.setattr("gaia.tools.browser.render.render_html_to_png", fake_render)
    wa = _FakeSender()
    gaia = _gaia(tmp_path, {"whatsapp": wa})
    task = Task(title="site", notify_channel="whatsapp", notify_chat="972@x")
    run = SoulRun(
        True, "s", "S", False, summary="built", workspace=str(tmp_path), files=["index.html"]
    )

    await present_result(gaia, task, run)

    medias = [r for r in wa.sent if isinstance(r, Media)]
    assert len(medias) == 1 and medias[0].path.name == "_preview.png"  # fallback rendered


async def test_no_connector_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_handler(monkeypatch, ["x"])
    gaia = _gaia(tmp_path, {})  # nothing live
    task = Task(title="site", notify_channel="whatsapp", notify_chat="972@x")

    await present_result(gaia, task, SoulRun(True, "s", "S", False, summary="b"))  # no raise

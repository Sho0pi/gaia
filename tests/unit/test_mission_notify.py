"""notify_result: target priority (notify field → owner → cron default), best-effort push."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.connectors.base import Media
from gaia.missions import Task
from gaia.missions.notify import notify_result
from gaia.souls.run import SoulRun
from gaia.users import UserStore


class _FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send_to(self, chat: str, reply: Any) -> None:
        self.sent.append((chat, reply))


def _gaia(connectors: dict[str, Any], users: UserStore, deliver: tuple[str, str] = ("", "")) -> Any:
    return SimpleNamespace(
        connectors=connectors,
        users=users,
        config=SimpleNamespace(
            cron=SimpleNamespace(deliver=SimpleNamespace(channel=deliver[0], chat=deliver[1]))
        ),
    )


async def test_notify_field_wins(tmp_path: Path) -> None:
    wa = _FakeSender()
    gaia = _gaia({"whatsapp": wa}, UserStore(tmp_path / "u.json"))
    task = Task(title="brief", notify_channel="whatsapp", notify_chat="972@x", owner="itay")

    await notify_result(gaia, task, SoulRun(True, "s", "S", False, summary="here it is"))

    assert wa.sent and wa.sent[0][0] == "972@x" and "here it is" in wa.sent[0][1]


async def test_falls_back_to_owner_identity(tmp_path: Path) -> None:
    wa = _FakeSender()
    users = UserStore(tmp_path / "u.json")
    users.register("whatsapp", "111@s.whatsapp.net", "Itay", role="admin")
    gaia = _gaia({"whatsapp": wa}, users)
    task = Task(title="x", owner="itay")  # no notify field

    await notify_result(gaia, task, SoulRun(True, "s", "S", False, summary="done"))

    assert wa.sent and wa.sent[0][0] == "111@s.whatsapp.net"


async def test_falls_back_to_cron_default(tmp_path: Path) -> None:
    tg = _FakeSender()
    gaia = _gaia({"telegram": tg}, UserStore(tmp_path / "u.json"), deliver=("telegram", "999"))
    task = Task(title="x")  # no notify, no owner

    await notify_result(gaia, task, SoulRun(True, "s", "S", False, summary="done"))

    assert tg.sent and tg.sent[0][0] == "999"


async def test_no_target_is_silent(tmp_path: Path) -> None:
    wa = _FakeSender()
    gaia = _gaia({"whatsapp": wa}, UserStore(tmp_path / "u.json"))
    task = Task(title="x")  # nothing to resolve

    await notify_result(gaia, task, SoulRun(True, "s", "S", False, summary="done"))

    assert wa.sent == []


async def test_connector_not_running_is_skipped(tmp_path: Path) -> None:
    gaia = _gaia({}, UserStore(tmp_path / "u.json"))  # whatsapp not live
    task = Task(title="x", notify_channel="whatsapp", notify_chat="972@x")

    await notify_result(gaia, task, SoulRun(True, "s", "S", False, summary="done"))  # no raise


async def test_text_artifact_content_is_delivered(tmp_path: Path) -> None:
    # The soul writes the real answer to a file and only summarizes "done" — the push must
    # carry the file content, not just the summary.
    (tmp_path / "report.md").write_text("# Plan\nDay 1: Push\nDay 2: Pull\n")
    wa = _FakeSender()
    gaia = _gaia({"whatsapp": wa}, UserStore(tmp_path / "u.json"))
    task = Task(title="research", notify_channel="whatsapp", notify_chat="972@x")
    run = SoulRun(
        True,
        "s",
        "S",
        False,
        summary="Done. Wrote report.md.",
        workspace=str(tmp_path),
        files=["report.md"],
    )

    await notify_result(gaia, task, run)

    msg = wa.sent[0][1]
    assert "Day 1: Push" in msg and "Day 2: Pull" in msg  # actual content delivered


async def test_web_deliverable_is_rendered_and_sent_as_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An index.html deliverable is rendered to a screenshot image — not dumped as source.
    (tmp_path / "index.html").write_text("<h1>Gym Site</h1>")

    async def fake_render(html: Path, out_png: Path) -> Path:
        out_png.write_bytes(b"PNG")  # noqa: ASYNC240 - test stub for a real render
        return out_png

    monkeypatch.setattr("gaia.tools.browser.render.render_html_to_png", fake_render)
    wa = _FakeSender()
    gaia = _gaia({"whatsapp": wa}, UserStore(tmp_path / "u.json"))
    task = Task(title="Build site", notify_channel="whatsapp", notify_chat="972@x")
    run = SoulRun(
        True, "s", "S", False, summary="Built it.", workspace=str(tmp_path), files=["index.html"]
    )

    await notify_result(gaia, task, run)

    assert len(wa.sent) == 1
    _, reply = wa.sent[0]
    assert isinstance(reply, Media) and reply.path.name == "_preview.png"
    assert "Built it." in reply.caption  # summary rides along as the caption
    assert "<h1>" not in str(wa.sent)  # raw html source was NOT dumped


async def test_web_deliverable_falls_back_when_render_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.html").write_text("<h1>Gym Site</h1>")

    async def no_render(html: Path, out_png: Path) -> None:
        return None  # playwright missing / render failed

    monkeypatch.setattr("gaia.tools.browser.render.render_html_to_png", no_render)
    wa = _FakeSender()
    gaia = _gaia({"whatsapp": wa}, UserStore(tmp_path / "u.json"))
    task = Task(title="Build site", notify_channel="whatsapp", notify_chat="972@x")
    run = SoulRun(
        True, "s", "S", False, summary="Built it.", workspace=str(tmp_path), files=["index.html"]
    )

    await notify_result(gaia, task, run)  # no crash

    assert wa.sent and isinstance(wa.sent[0][1], str) and "Built it." in wa.sent[0][1]


async def test_image_artifacts_sent_as_media(tmp_path: Path) -> None:
    wa = _FakeSender()
    gaia = _gaia({"whatsapp": wa}, UserStore(tmp_path / "u.json"))
    task = Task(title="shot", notify_channel="whatsapp", notify_chat="972@x")
    run = SoulRun(True, "s", "S", False, summary="ok", workspace="/w", files=["out.png", "a.md"])

    await notify_result(gaia, task, run)

    medias = [r for _, r in wa.sent if isinstance(r, Media)]
    assert len(medias) == 1 and medias[0].path == Path("/w/out.png")  # only the image

"""notify_result: target priority (notify field → owner → cron default), best-effort push."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

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


async def test_image_artifacts_sent_as_media(tmp_path: Path) -> None:
    wa = _FakeSender()
    gaia = _gaia({"whatsapp": wa}, UserStore(tmp_path / "u.json"))
    task = Task(title="shot", notify_channel="whatsapp", notify_chat="972@x")
    run = SoulRun(True, "s", "S", False, summary="ok", workspace="/w", files=["out.png", "a.md"])

    await notify_result(gaia, task, run)

    medias = [r for _, r in wa.sent if isinstance(r, Media)]
    assert len(medias) == 1 and medias[0].path == Path("/w/out.png")  # only the image

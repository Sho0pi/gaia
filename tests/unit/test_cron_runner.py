"""The cron runner: fired job → handler turn → proactive delivery routing."""

from __future__ import annotations

from typing import Any

import pytest

from gaia.config import Settings
from gaia.connectors.base import Reply
from gaia.cron.runner import make_runner
from gaia.cron.store import CronJob


class _FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Reply]] = []

    async def send_to(self, chat: str, reply: Reply) -> None:
        self.sent.append((chat, reply))


def _gaia(tmp_path: Any, deliver: str = "") -> Any:
    from gaia.core import Gaia

    config = tmp_path / "gaia.yaml"
    config.write_text(f"memory:\n  enabled: false\n{deliver}")
    return Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config))


@pytest.fixture
def fake_handler(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the heavy handler with one that echoes the prompt to send."""
    prompts: list[str] = []

    def build(gaia: Any, **kw: Any) -> Any:
        async def handler(text: str, send: Any) -> None:
            prompts.append(text)
            await send("the result")

        return handler

    monkeypatch.setattr("gaia.core.handler.build_handler", build)
    return prompts


async def test_fired_job_delivers_to_its_chat(tmp_path: Any, fake_handler: list[str]) -> None:
    gaia = _gaia(tmp_path)
    telegram = _FakeSender()
    run = make_runner(gaia, {"telegram": telegram})
    job = CronJob(kind="every", expr="60", message="AI brief", channel="telegram", chat="123")

    await run(job)

    assert telegram.sent == [("123", "the result")]
    assert "[scheduled task" in fake_handler[0]  # the model knows it's unprompted
    assert "AI brief" in fake_handler[0]


async def test_fired_job_falls_back_to_configured_default(
    tmp_path: Any, fake_handler: list[str]
) -> None:
    gaia = _gaia(tmp_path, deliver="cron:\n  deliver:\n    channel: telegram\n    chat: '999'\n")
    telegram = _FakeSender()
    run = make_runner(gaia, {"telegram": telegram})
    job = CronJob(kind="every", expr="60", message="x")  # no captured chat (CLI-created)

    await run(job)

    assert telegram.sent == [("999", "the result")]


async def test_missing_connector_drops_with_log(
    tmp_path: Any, fake_handler: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    gaia = _gaia(tmp_path)
    run = make_runner(gaia, {})  # nothing running
    job = CronJob(kind="every", expr="60", message="x", channel="telegram", chat="123")

    with caplog.at_level(logging.WARNING, logger="gaia.cron.runner"):
        await run(job)  # no crash

    assert "dropped" in caplog.text

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
        async def handler(inbound: Any, send: Any) -> None:
            prompts.append(inbound.text)
            await send("the result")

        return handler

    monkeypatch.setattr("gaia.core.handler.build_handler", build)
    return prompts


async def test_fired_job_delivers_to_its_chat(tmp_path: Any, fake_handler: list[str]) -> None:
    gaia = _gaia(tmp_path)
    telegram = _FakeSender()
    gaia.connectors["telegram"] = telegram  # @inject pulls gaia.container.connectors()
    run = make_runner(gaia)
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
    gaia.connectors["telegram"] = telegram
    run = make_runner(gaia)
    job = CronJob(kind="every", expr="60", message="x")  # no captured chat (CLI-created)

    await run(job)

    assert telegram.sent == [("999", "the result")]


async def test_missing_connector_drops_with_log(
    tmp_path: Any, fake_handler: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    gaia = _gaia(tmp_path)  # nothing in gaia.connectors
    run = make_runner(gaia)
    job = CronJob(kind="every", expr="60", message="x", channel="telegram", chat="123")

    with caplog.at_level(logging.WARNING, logger="gaia.cron.runner"):
        await run(job)  # no crash

    assert "dropped" in caplog.text


async def test_two_gaias_keep_separate_connectors(tmp_path: Any, fake_handler: list[str]) -> None:
    """Each Gaia's runner delivers through ITS OWN connectors (per-instance isolation).

    Regression guard for the #146 @inject spike: the conclusion was to read
    ``gaia.connectors`` explicitly rather than dependency-injector ``@inject`` (whose
    ``wire()`` is module-global and can't see per-call closures). This proves two Gaias
    in one process don't cross-deliver — exactly what global wiring would have broken.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    g1 = _gaia(tmp_path / "a")
    s1 = _FakeSender()
    g1.connectors["telegram"] = s1
    run1 = make_runner(g1)

    g2 = _gaia(tmp_path / "b")  # wires the module to g2's container
    s2 = _FakeSender()
    g2.connectors["telegram"] = s2
    run2 = make_runner(g2)

    job = CronJob(kind="every", expr="60", message="x", channel="telegram", chat="1")
    await run1(job)
    await run2(job)

    assert s1.sent == [("1", "the result")]  # g1 delivered through g1's connector
    assert s2.sent == [("1", "the result")]

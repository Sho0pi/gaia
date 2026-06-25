"""Monitor loop: run_cycle filters ignore-actions, dedups, and notifies on new findings."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gaia.config.schema import MonitorConfig
from gaia.monitor import loop as mloop
from gaia.monitor.analyst import Finding, HealthReport


def _gaia(notify: bool = True) -> Any:
    return SimpleNamespace(config=SimpleNamespace(monitor=MonitorConfig(notify=notify)))


def _report(*findings: Finding) -> HealthReport:
    return HealthReport(summary="window summary", findings=list(findings))


async def test_filters_ignore_and_notifies_new(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(
        Finding(
            title="bug",
            severity="critical",
            signature="ValueError @ h.py:1",
            action="file_issue",
            summary="x",
        ),
        Finding(title="noise", signature="Timeout @ web", action="ignore", summary=""),
    )

    async def fake_analyze(_g: Any) -> HealthReport:
        return report

    sent: list[list[Finding]] = []

    async def fake_notify(_g: Any, _summary: str, findings: list[Finding]) -> None:
        sent.append(findings)

    monkeypatch.setattr(mloop, "analyze", fake_analyze)
    monkeypatch.setattr(mloop, "_notify_admin", fake_notify)

    out = await mloop.run_cycle(_gaia())
    assert [f.title for f in out] == ["bug"]  # ignore-action dropped
    assert sent and [f.title for f in sent[0]] == ["bug"]  # DM'd the real one


async def test_dedups_on_second_run(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(Finding(title="bug", signature="V @ h:1", action="notify", summary=""))

    async def fake_analyze(_g: Any) -> HealthReport:
        return report

    async def fake_notify(_g: Any, _s: str, _f: list[Finding]) -> None:
        return None

    monkeypatch.setattr(mloop, "analyze", fake_analyze)
    monkeypatch.setattr(mloop, "_notify_admin", fake_notify)

    first = await mloop.run_cycle(_gaia())
    second = await mloop.run_cycle(_gaia())
    assert len(first) == 1 and second == []  # same signature suppressed next cycle


async def test_healthy_window_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_analyze(_g: Any) -> None:
        return None

    monkeypatch.setattr(mloop, "analyze", fake_analyze)
    assert await mloop.run_cycle(_gaia()) == []


async def test_notify_off_still_returns_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(Finding(title="bug", signature="V @ h:2", action="notify", summary=""))

    async def fake_analyze(_g: Any) -> HealthReport:
        return report

    called = False

    async def fake_notify(_g: Any, _s: str, _f: list[Finding]) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(mloop, "analyze", fake_analyze)
    monkeypatch.setattr(mloop, "_notify_admin", fake_notify)

    out = await mloop.run_cycle(_gaia(notify=False))
    assert [f.title for f in out] == ["bug"] and called is False  # found, but not DM'd

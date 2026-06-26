"""Monitor loop: run_cycle filters ignore-actions, dedups, and notifies on new findings."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gaia.config.schema import MonitorConfig
from gaia.monitor import loop as mloop
from gaia.monitor.analyst import Finding, HealthReport


def test_report_coerces_nonstring_fields_from_flaky_models() -> None:
    # Small models sometimes return a dict/None where a string is expected — must not crash.
    report = HealthReport.model_validate(
        {
            "summary": {"window": "x", "events": 6},  # dict instead of str
            "findings": [{"signature": "K @ h:1", "title": None, "action": "notify"}],  # None title
        }
    )
    assert isinstance(report.summary, str) and report.summary  # coerced, non-empty
    assert report.findings[0].title == "" and report.findings[0].signature == "K @ h:1"


def _gaia(notify: bool = True) -> Any:
    return SimpleNamespace(config=SimpleNamespace(monitor=MonitorConfig(notify=notify)))


async def test_analyze_skips_on_invalid_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    # Weak models sometimes return malformed JSON — analyze must skip, not crash or log_error.
    from datetime import datetime

    g = SimpleNamespace(
        config=SimpleNamespace(monitor=MonitorConfig()),
        settings=SimpleNamespace(log_dir="/x"),
    )
    monkeypatch.setattr(
        "gaia.analysis.events.read_events",
        lambda *_a, **_k: [
            {"message": "turn_error", "error": "X", "where": "h:1", "_ts": datetime.now()}
        ],
    )

    async def boom(*_a: Any, **_k: Any) -> Any:
        HealthReport.model_validate_json("{")  # raises ValidationError (truncated JSON)

    monkeypatch.setattr(mloop, "_run_analyst", boom)
    assert await mloop.analyze(g) is None  # graceful skip


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

    async def fake_notify(_g: Any, _summary: str, findings: list[Finding], _filed: Any) -> None:
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

    async def fake_notify(_g: Any, _s: str, _f: list[Finding], _filed: Any) -> None:
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


def _gh_gaia(token: str | None) -> Any:
    from gaia.config.schema import MonitorGithubConfig

    github = MonitorGithubConfig(create_issues=True, repo="o/r", label="L")
    return SimpleNamespace(
        config=SimpleNamespace(monitor=SimpleNamespace(github=github)),
        settings=SimpleNamespace(github_token=token),
    )


async def test_file_issues_skips_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_file_issue(*_a: Any, **_k: Any) -> str:
        nonlocal called
        called = True
        return "url"

    monkeypatch.setattr("gaia.monitor.github.file_issue", fake_file_issue)
    await mloop._file_issues(
        _gh_gaia(token=None), [Finding(title="b", action="file_issue", signature="s")]
    )
    assert called is False  # create_issues on but no token -> skipped, never crashes


async def test_file_issues_files_only_file_issue_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_file_issue(repo: str, token: str, **_k: Any) -> str:
        calls.append((repo, token))
        return "https://x/issues/1"

    monkeypatch.setattr("gaia.monitor.github.file_issue", fake_file_issue)
    await mloop._file_issues(
        _gh_gaia(token="tok"),
        [
            Finding(title="b", action="file_issue", signature="s"),
            Finding(title="n", action="notify"),
        ],
    )
    assert calls == [("o/r", "tok")]  # only the file_issue finding, with repo + token


async def test_notify_off_still_returns_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(Finding(title="bug", signature="V @ h:2", action="notify", summary=""))

    async def fake_analyze(_g: Any) -> HealthReport:
        return report

    called = False

    async def fake_notify(_g: Any, _s: str, _f: list[Finding], _filed: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(mloop, "analyze", fake_analyze)
    monkeypatch.setattr(mloop, "_notify_admin", fake_notify)

    out = await mloop.run_cycle(_gaia(notify=False))
    assert [f.title for f in out] == ["bug"] and called is False  # found, but not DM'd

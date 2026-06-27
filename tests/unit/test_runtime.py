"""ensure_runtime_deps: idempotent runtime provisioning (mocked — no real installs)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia import runtime


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every subprocess.run target (argv list or shell string); never run anything."""
    seen: list[Any] = []

    def fake_run(cmd: Any, **_kw: Any) -> Any:
        seen.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    return seen


def test_installs_browser_when_bunx_present(
    monkeypatch: pytest.MonkeyPatch, calls: list[Any]
) -> None:
    monkeypatch.setattr("gaia.mcp._resolve_runtime", lambda _n: "/opt/bunx")
    notes = runtime.ensure_runtime_deps(Path("/v/bin/python"))

    assert ["/opt/bunx", "@playwright/mcp@latest", "install-browser", "chrome-for-testing"] in calls
    assert "playwright-mcp browser ready" in notes


def test_installs_bun_when_missing(monkeypatch: pytest.MonkeyPatch, calls: list[Any]) -> None:
    monkeypatch.setattr("gaia.mcp._resolve_runtime", lambda _n: None)  # bun absent before + after
    notes = runtime.ensure_runtime_deps(Path("/v/bin/python"))

    assert any(isinstance(c, str) and "bun.sh/install" in c for c in calls)  # the bun installer ran
    assert "installing bun…" in notes
    # bunx still unresolved → the browser step is skipped (no install-browser call)
    assert not any(isinstance(c, list) and "install-browser" in c for c in calls)


def test_browser_false_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime.subprocess, "run", lambda *a, **k: pytest.fail("must not run anything")
    )
    assert runtime.ensure_runtime_deps(Path("/v/bin/python"), browser=False) == []

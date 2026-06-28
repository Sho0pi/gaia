"""ensure_runtime_deps: backend-aware runtime provisioning (mocked — no real installs)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia import runtime
from gaia.config.schema import BrowserConfig


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every subprocess.run target (argv list or shell string); never run anything."""
    seen: list[Any] = []

    def fake_run(cmd: Any, **_kw: Any) -> Any:
        seen.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    return seen


def _has(calls: list[Any], needle: str) -> bool:
    return any(isinstance(c, list) and needle in c for c in calls)


def _has_camoufox_fetch(calls: list[Any]) -> bool:
    return any(isinstance(c, list) and c[-2:] == ["camoufox", "fetch"] for c in calls)


# --- native + camoufox (the default) -----------------------------------------------


def test_default_native_camoufox_fetches_only_camoufox(calls: list[Any]) -> None:
    # the `calls` fake returns empty stdout → _camoufox_installed() reads "not installed"
    notes = runtime.ensure_runtime_deps(Path("/v/bin/python"), BrowserConfig())  # default native

    assert _has_camoufox_fetch(calls) and "camoufox browser ready" in notes
    # nothing for the other backends/engines
    assert not _has(calls, "install-browser") and not _has(calls, "install")


def test_camoufox_fetch_skipped_when_already_installed(
    monkeypatch: pytest.MonkeyPatch, calls: list[Any]
) -> None:
    monkeypatch.setattr(runtime, "_camoufox_installed", lambda _p: True)  # already there

    runtime.ensure_runtime_deps(Path("/v/bin/python"), BrowserConfig())

    assert not _has_camoufox_fetch(calls)  # the ~700MB re-fetch is skipped


# --- mcp backend (opt-in) ----------------------------------------------------------


def test_mcp_backend_installs_bun_and_mcp_browser(
    monkeypatch: pytest.MonkeyPatch, calls: list[Any]
) -> None:
    monkeypatch.setattr("gaia.mcp._resolve_runtime", lambda _n: "/opt/bunx")  # bunx present
    cfg = BrowserConfig(backend="mcp")

    notes = runtime.ensure_runtime_deps(Path("/v/bin/python"), cfg)

    assert ["/opt/bunx", "@playwright/mcp@latest", "install-browser", "chrome-for-testing"] in calls
    assert "playwright-mcp browser ready" in notes
    assert not _has_camoufox_fetch(calls)  # mcp doesn't pull camoufox


def test_mcp_requested_but_bunx_missing_falls_back_to_native(
    monkeypatch: pytest.MonkeyPatch, calls: list[Any]
) -> None:
    monkeypatch.setattr("gaia.mcp._resolve_runtime", lambda _n: None)  # no bunx → native fallback
    cfg = BrowserConfig(backend="mcp")  # but engine defaults camoufox

    runtime.ensure_runtime_deps(Path("/v/bin/python"), cfg)

    assert not _has(calls, "install-browser")  # mcp browser not installed
    assert _has_camoufox_fetch(calls)  # provisions the native camoufox it fell back to


# --- native + chromium -------------------------------------------------------------


def test_native_chromium_installs_chromium(calls: list[Any], tmp_path: Path) -> None:
    (tmp_path / "playwright").touch()  # the venv's playwright CLI exists
    cfg = BrowserConfig(backend="native", engine="chromium")

    notes = runtime.ensure_runtime_deps(tmp_path / "python", cfg)

    assert [str(tmp_path / "playwright"), "install", "chromium"] in calls
    assert "native chromium ready" in notes
    assert not _has_camoufox_fetch(calls)

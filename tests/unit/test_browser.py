"""Unit tests for the browser tools — driven with a fake page, no real Chromium."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from godpy.tools import browser
from godpy.tools.browser.base import BrowserSessionManager, normalize_ref, parse_refs, truncate

# A canned AI-mode aria snapshot: a textbox (e1) and a button (e2).
_SNAPSHOT = '- generic [ref=e3]:\n  - textbox "q" [ref=e1]\n  - button "Go" [ref=e2]'


class _BodyLocator:
    def __init__(self, text: str) -> None:
        self._text = text

    async def aria_snapshot(self, *, mode: str | None = None) -> str:
        return self._text


class _ActionLocator:
    def __init__(self) -> None:
        self.clicked = False
        self.filled: str | None = None
        self.pressed: str | None = None
        self.screenshot_path: str | None = None

    async def click(self) -> None:
        self.clicked = True

    async def fill(self, text: str) -> None:
        self.filled = text

    async def press(self, key: str) -> None:
        self.pressed = key

    async def screenshot(self, path: str) -> None:
        self.screenshot_path = path
        Path(path).write_bytes(b"\x89PNG fake")  # noqa: ASYNC240 - test fake, not real I/O


class _FakePage:
    """The slice of a Playwright page the browser tools call."""

    def __init__(
        self, *, url: str = "https://example.com", title: str = "Example", snapshot: str = _SNAPSHOT
    ) -> None:
        self.url = url
        self._title = title
        self._snapshot = snapshot
        self.action_locators: dict[str, _ActionLocator] = {}
        self.goto_url: str | None = None
        self.screenshot_path: str | None = None
        self.screenshot_full_page: bool | None = None  # None until a page screenshot is taken

    async def goto(self, url: str) -> None:
        self.goto_url = url
        self.url = url

    async def title(self) -> str:
        return self._title

    def locator(self, selector: str) -> Any:
        if selector == "body":
            return _BodyLocator(self._snapshot)
        if selector.startswith("aria-ref="):
            ref = selector.split("=", 1)[1]
            return self.action_locators.setdefault(ref, _ActionLocator())
        raise AssertionError(f"unexpected selector {selector!r}")

    async def screenshot(self, path: str, full_page: bool = False) -> None:
        self.screenshot_path = path
        self.screenshot_full_page = full_page
        Path(path).write_bytes(b"\x89PNG fake")  # noqa: ASYNC240 - test fake, not real I/O


class _FakeToolContext:
    def __init__(self, agent: str = "tester") -> None:
        self.agent_name = agent


def _manager_with(page: _FakePage) -> BrowserSessionManager:
    async def launcher() -> tuple[Any, Any]:
        async def close() -> None:
            page.closed = True  # type: ignore[attr-defined]

        return page, close

    return BrowserSessionManager(launcher)


# --- pure helpers -----------------------------------------------------------------


def test_parse_refs_pulls_ref_ids() -> None:
    assert parse_refs(_SNAPSHOT) == {"e1", "e2", "e3"}
    assert parse_refs("no refs here") == set()


def test_normalize_ref_strips_at_and_space() -> None:
    assert normalize_ref(" @e4 ") == "e4"
    assert normalize_ref("e4") == "e4"


def test_truncate_flags_when_cut() -> None:
    text, cut = truncate("x" * 50, cap=10)
    assert cut is True and len(text) == 10
    text, cut = truncate("short", cap=10)
    assert cut is False and text == "short"


# --- navigate ---------------------------------------------------------------------


async def test_navigate_returns_title() -> None:
    page = _FakePage(url="https://example.com", title="Example Domain")
    tool = browser.make_browser_navigate(_manager_with(page))

    result = await tool("https://example.com", tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert result["title"] == "Example Domain"
    assert page.goto_url == "https://example.com"


async def test_navigate_rejects_blocked_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # SSRF guard reuses web_fetch.validate_url; force the metadata IP to resolve there.
    monkeypatch.setattr("godpy.tools.web_fetch._resolve_ips", lambda host: ["169.254.169.254"])
    page = _FakePage()
    tool = browser.make_browser_navigate(_manager_with(page))

    result = await tool("http://metadata.local", tool_context=_FakeToolContext())

    assert result["status"] == "error"
    assert "blocked" in result["error_message"]
    assert page.goto_url is None  # never navigated


async def test_navigate_empty_url_errors() -> None:
    tool = browser.make_browser_navigate(_manager_with(_FakePage()))
    result = await tool("   ", tool_context=_FakeToolContext())
    assert result["status"] == "error"


# --- snapshot ---------------------------------------------------------------------


async def test_snapshot_returns_text_and_stores_refs() -> None:
    page = _FakePage(title="Home")
    manager = _manager_with(page)
    snap = browser.make_browser_snapshot(manager)

    result = await snap(tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert "[ref=e2]" in result["snapshot"]
    session = await manager.get("tester")
    assert session.refs == {"e1", "e2", "e3"}  # available for click/type


async def test_snapshot_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("godpy.tools.browser.snapshot.truncate", lambda t: (t[:5], True))
    snap = browser.make_browser_snapshot(_manager_with(_FakePage()))

    result = await snap(tool_context=_FakeToolContext())

    assert result["truncated"] is True


# --- click / type -----------------------------------------------------------------


async def test_click_resolves_known_ref() -> None:
    page = _FakePage()
    manager = _manager_with(page)
    await browser.make_browser_snapshot(manager)(tool_context=_FakeToolContext())
    click = browser.make_browser_click(manager)

    result = await click("e2", tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert page.action_locators["e2"].clicked is True


async def test_click_unknown_ref_errors() -> None:
    page = _FakePage()
    manager = _manager_with(page)
    await browser.make_browser_snapshot(manager)(tool_context=_FakeToolContext())
    click = browser.make_browser_click(manager)

    result = await click("e99", tool_context=_FakeToolContext())

    assert result["status"] == "error"
    assert "unknown ref" in result["error_message"]


async def test_type_fills_and_submits() -> None:
    page = _FakePage()
    manager = _manager_with(page)
    await browser.make_browser_snapshot(manager)(tool_context=_FakeToolContext())
    type_tool = browser.make_browser_type(manager)

    result = await type_tool("e1", "hello", submit=True, tool_context=_FakeToolContext())

    assert result["status"] == "success"
    loc = page.action_locators["e1"]
    assert loc.filled == "hello" and loc.pressed == "Enter"


async def test_type_does_not_log_secret_text(caplog: pytest.LogCaptureFixture) -> None:
    page = _FakePage()
    manager = _manager_with(page)
    await browser.make_browser_snapshot(manager)(tool_context=_FakeToolContext())
    type_tool = browser.make_browser_type(manager)

    with caplog.at_level(logging.INFO, logger="godpy.events"):
        await type_tool("e1", "hunter2-secret", tool_context=_FakeToolContext())

    assert "hunter2-secret" not in caplog.text


# --- screenshot -------------------------------------------------------------------


async def test_screenshot_writes_png_full_page_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("godpy.constants.AGENTS_DIR", tmp_path / "agents")
    page = _FakePage()
    shot = browser.make_browser_screenshot(_manager_with(page))

    result = await shot(tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert Path(result["path"]).is_file()  # noqa: ASYNC240 - assertion, not hot-path I/O
    assert result["path"].endswith(".png")
    assert page.screenshot_full_page is True  # the whole scrollable page, not just viewport


async def test_screenshot_viewport_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("godpy.constants.AGENTS_DIR", tmp_path / "agents")
    page = _FakePage()
    shot = browser.make_browser_screenshot(_manager_with(page))

    result = await shot(full_page=False, tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert page.screenshot_full_page is False


async def test_screenshot_of_a_single_element(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("godpy.constants.AGENTS_DIR", tmp_path / "agents")
    page = _FakePage()
    manager = _manager_with(page)
    await browser.make_browser_snapshot(manager)(tool_context=_FakeToolContext())
    shot = browser.make_browser_screenshot(manager)

    result = await shot(ref="e2", tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert page.action_locators["e2"].screenshot_path == result["path"]  # element, not page
    assert page.screenshot_full_page is None  # whole-page screenshot not taken


# --- session lifecycle ------------------------------------------------------------


async def test_close_all_tears_sessions_down() -> None:
    page = _FakePage()
    manager = _manager_with(page)
    await manager.get("a")
    await manager.get("b")

    await manager.close_all()

    assert page.closed is True  # type: ignore[attr-defined]
    assert manager._sessions == {}

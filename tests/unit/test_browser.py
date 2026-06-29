"""Unit tests for the browser tools — driven with a fake page, no real Chromium."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gaia.tools import browser
from gaia.tools.browser.base import BrowserSessionManager, normalize_ref, parse_refs, truncate

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


class _FakeMouse:
    def __init__(self) -> None:
        self.wheel_dy: int | None = None

    async def wheel(self, dx: int, dy: int) -> None:
        self.wheel_dy = dy


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: str | None = None

    async def press(self, key: str) -> None:
        self.pressed = key


class _FakePage:
    """The slice of a Playwright page the browser tools call."""

    def __init__(
        self,
        *,
        url: str = "https://example.com",
        title: str = "Example",
        snapshot: str = _SNAPSHOT,
        eval_result: Any = None,
    ) -> None:
        self.url = url
        self._title = title
        self._snapshot = snapshot
        self._eval_result = eval_result
        self.action_locators: dict[str, _ActionLocator] = {}
        self.goto_url: str | None = None
        self.went_back = False
        self.screenshot_path: str | None = None
        self.screenshot_full_page: bool | None = None  # None until a page screenshot is taken
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.handlers: dict[str, list[Any]] = {}  # event -> handlers (on/once)
        self.alive = True  # set False to simulate a crashed browser (evaluate raises)

    async def goto(self, url: str) -> None:
        self.goto_url = url
        self.url = url

    async def go_back(self) -> None:
        self.went_back = True

    async def title(self) -> str:
        return self._title

    async def evaluate(self, expression: str) -> Any:
        if not self.alive:
            raise RuntimeError("Connection closed while reading from the driver")
        return self._eval_result

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def once(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, arg: Any) -> None:
        """Test helper: fire a stored event handler (console/pageerror/dialog)."""
        for handler in self.handlers.get(event, []):
            handler(arg)

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
    monkeypatch.setattr("gaia.tools.web_fetch._resolve_ips", lambda host: ["169.254.169.254"])
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
    monkeypatch.setattr("gaia.tools.browser.base.truncate", lambda t: (t[:5], True))
    snap = browser.make_browser_snapshot(_manager_with(_FakePage()))

    result = await snap(tool_context=_FakeToolContext())

    assert result["truncated"] is True


async def test_action_returns_fresh_snapshot() -> None:
    # #90: click/type/etc fold the post-action snapshot into their result (saves a snapshot turn).
    page = _FakePage()
    manager = _manager_with(page)
    await browser.make_browser_snapshot(manager)(tool_context=_FakeToolContext())

    result = await browser.make_browser_click(manager)("e2", tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert "[ref=e2]" in result["snapshot"]  # the page came back with the action


async def test_navigate_returns_snapshot_and_title() -> None:
    page = _FakePage(title="Home")
    result = await browser.make_browser_navigate(_manager_with(page))(
        "https://example.com", tool_context=_FakeToolContext()
    )

    assert result["status"] == "success" and result["title"] == "Home"
    assert "snapshot" in result  # navigate hands back the page too


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


# --- screenshot -------------------------------------------------------------------


async def test_screenshot_is_viewport_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default viewport (normal aspect) — a full-page shot is tall and chat apps crop it.
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    page = _FakePage()
    shot = browser.make_browser_screenshot(_manager_with(page))

    result = await shot(tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert Path(result["path"]).is_file()  # noqa: ASYNC240 - assertion, not hot-path I/O
    assert result["path"].endswith(".png")
    assert page.screenshot_full_page is False  # the visible viewport, not the whole page


async def test_screenshot_full_page_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    page = _FakePage()
    shot = browser.make_browser_screenshot(_manager_with(page))

    result = await shot(full_page=True, tool_context=_FakeToolContext())

    assert result["status"] == "success"
    assert page.screenshot_full_page is True


async def test_screenshot_of_a_single_element(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
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


# --- scroll / press / back --------------------------------------------------------


async def test_scroll_down_then_up() -> None:
    page = _FakePage()
    scroll = browser.make_browser_scroll(_manager_with(page))

    assert (await scroll(tool_context=_FakeToolContext()))["status"] == "success"
    assert page.mouse.wheel_dy and page.mouse.wheel_dy > 0  # default down

    await scroll(direction="up", amount=300, tool_context=_FakeToolContext())
    assert page.mouse.wheel_dy == -300


async def test_press_sends_key() -> None:
    page = _FakePage()
    press = browser.make_browser_press(_manager_with(page))

    result = await press("Enter", tool_context=_FakeToolContext())

    assert result["status"] == "success" and page.keyboard.pressed == "Enter"


async def test_back_goes_back() -> None:
    page = _FakePage()
    back = browser.make_browser_back(_manager_with(page))

    result = await back(tool_context=_FakeToolContext())

    assert result["status"] == "success" and page.went_back is True


# --- get_images / evaluate --------------------------------------------------------


async def test_get_images_lists_them() -> None:
    imgs = [{"src": "https://x/a.png", "alt": "A"}, {"src": "https://x/b.png", "alt": ""}]
    page = _FakePage(eval_result=imgs)
    get_images = browser.make_browser_get_images(_manager_with(page))

    result = await get_images(tool_context=_FakeToolContext())

    assert result["status"] == "success" and result["count"] == 2 and result["images"] == imgs


async def test_evaluate_returns_result() -> None:
    page = _FakePage(eval_result="Example Domain")
    evaluate = browser.make_browser_evaluate(_manager_with(page))

    result = await evaluate("document.title", tool_context=_FakeToolContext())

    assert result["status"] == "success" and result["result"] == "Example Domain"


# --- console / dialog -------------------------------------------------------------


async def test_console_returns_and_clears_buffer() -> None:
    from types import SimpleNamespace

    page = _FakePage()
    manager = _manager_with(page)
    await manager.get("tester")  # creates the session + wires the console listener
    page.emit("console", SimpleNamespace(type="error", text="boom"))
    page.emit("pageerror", "ReferenceError: x")

    console = browser.make_browser_console(manager)
    result = await console(tool_context=_FakeToolContext())

    assert result["status"] == "success" and result["count"] == 2
    assert "[error] boom" in result["messages"]
    assert "[pageerror] ReferenceError: x" in result["messages"]
    # buffer cleared
    assert (await console(tool_context=_FakeToolContext()))["count"] == 0


async def test_dialog_arms_one_shot_handler() -> None:
    page = _FakePage()
    manager = _manager_with(page)
    await manager.get("tester")
    dialog = browser.make_browser_dialog(manager)

    result = await dialog(action="accept", tool_context=_FakeToolContext())

    assert result["status"] == "success" and result["armed"] == "accept"
    assert page.handlers.get("dialog")  # a handler is registered for the next dialog


# --- engine launcher --------------------------------------------------------------


def test_make_launcher_chromium() -> None:
    from types import SimpleNamespace

    from gaia.tools.browser.base import make_launcher

    assert callable(make_launcher(SimpleNamespace(engine="chromium", viewport="1280x800")))
    # an unknown engine also falls back to chromium
    assert callable(make_launcher(SimpleNamespace(engine="other", viewport="")))


def test_make_launcher_camoufox_builds_a_distinct_launcher() -> None:
    from types import SimpleNamespace

    chromium = browser.make_launcher(SimpleNamespace(engine="chromium", viewport=""))
    camoufox = browser.make_launcher(
        SimpleNamespace(engine="camoufox", headless=True, humanize=True, viewport="412x915")
    )
    assert callable(camoufox) and camoufox is not chromium


# --- recovery: a crashed browser self-heals -------------------------------------


async def test_dead_browser_session_is_relaunched() -> None:
    # Camoufox/Chromium can die mid-session; the next get() must relaunch, not reuse the dead page.
    launched: list[_FakePage] = []

    async def launcher() -> tuple[Any, Any]:
        page = _FakePage()
        launched.append(page)

        async def close() -> None:
            page.closed = True  # type: ignore[attr-defined]

        return page, close

    manager = BrowserSessionManager(launcher)
    s1 = await manager.get("a")  # launch #1
    s1.page.alive = False  # the browser crashes (its driver connection dies)

    s2 = await manager.get("a")  # liveness probe fails → drop + relaunch

    assert len(launched) == 2  # a fresh browser was launched
    assert s2.page is not s1.page  # not the dead one
    assert s1.page.closed is True  # the dead session was closed


async def test_close_survives_a_dead_browser() -> None:
    # Closing a crashed browser must not raise out of the manager.
    async def launcher() -> tuple[Any, Any]:
        page = _FakePage()

        async def close() -> None:
            raise RuntimeError("Connection closed while reading from the driver")

        return page, close

    manager = BrowserSessionManager(launcher)
    await manager.get("a")
    await manager.close("a")  # must not raise
    assert manager._sessions == {}


async def test_action_can_skip_snapshot() -> None:
    # snapshot=False lets the model save tokens when it doesn't need the page back.
    page = _FakePage()
    manager = _manager_with(page)
    await browser.make_browser_snapshot(manager)(tool_context=_FakeToolContext())

    result = await browser.make_browser_click(manager)(
        "e2", snapshot=False, tool_context=_FakeToolContext()
    )

    assert result["status"] == "success" and "snapshot" not in result


# --- viewport (phone-portrait default) --------------------------------------------


def test_parse_viewport() -> None:
    from types import SimpleNamespace

    from gaia.tools.browser.base import _parse_viewport

    assert _parse_viewport(SimpleNamespace(viewport="412x915")) == (412, 915)
    assert _parse_viewport(SimpleNamespace(viewport="1280x800")) == (1280, 800)
    assert _parse_viewport(SimpleNamespace(viewport="")) is None  # unset = engine default
    assert _parse_viewport(SimpleNamespace(viewport="garbage")) is None


def test_default_viewport_is_phone_portrait() -> None:
    from gaia.config.schema import BrowserConfig
    from gaia.tools.browser.base import _camoufox_opts, _parse_viewport

    cfg = BrowserConfig()  # default
    w, h = _parse_viewport(cfg)  # type: ignore[misc]
    assert w < h  # portrait, so chat previews don't crop it
    assert _camoufox_opts(cfg)["window"] == (w, h)  # camoufox gets a consistent window

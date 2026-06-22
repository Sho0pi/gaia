"""gocat-style log renderer (gaia.logfmt) — shared by the live console and `gaia logs`."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gaia.logfmt import TAG_WIDTH, level_badge, render_line, supports_color, tag_color


def test_supports_color_honours_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    tty = SimpleNamespace(isatty=lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert supports_color(tty) is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert supports_color(tty) is False


def test_supports_color_false_for_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert supports_color(SimpleNamespace(isatty=lambda: False)) is False


def _plain(*, ts: str = "12:00:00", tag: str = "gaia", level: str = "INFO", **kw: object) -> str:
    return render_line(ts=ts, tag=tag, level=level, body="msg", color=False, **kw)  # type: ignore[arg-type]


def test_tag_color_is_stable_per_name() -> None:
    assert tag_color("frontend_developer") == tag_color("frontend_developer")


def test_plain_has_no_ansi() -> None:
    line = render_line(ts="12:00:00", tag="gaia", level="INFO", body="hello", color=False)
    assert "\033[" not in line
    assert "gaia" in line and "hello" in line


def test_colored_has_ansi() -> None:
    line = render_line(ts="12:00:00", tag="gaia", level="ERROR", body="boom", color=True)
    assert "\033[" in line and "boom" in line


def test_tag_shown_on_every_line() -> None:
    # The actor tag is never blanked — you always see which agent a log belongs to.
    a = render_line(ts="12:00:00", tag="gaia", level="INFO", body="one", color=False)
    b = render_line(ts="12:00:01", tag="gaia", level="INFO", body="two", color=False)
    assert "gaia" in a and "gaia" in b


def test_badge_letter_per_level() -> None:
    assert level_badge("WARNING", color=False) == "W"
    assert level_badge("ERROR", color=False) == "E"
    assert level_badge("INFO", color=False) == "I"


def test_long_tag_truncated_with_ellipsis() -> None:
    long = "x" * (TAG_WIDTH + 10)
    line = render_line(ts="12:00:00", tag=long, level="INFO", body="m", color=False)
    assert "…" in line and ("x" * (TAG_WIDTH + 10)) not in line


def test_module_segment_follows_the_tag() -> None:
    # agent/project is the colored tag; the module (action/logger) rides after it, ·-prefixed.
    line = render_line(
        ts="12:00:00",
        tag="frontend_developer/pasta",
        level="INFO",
        body="",
        module="tool_used",
        fields={"tool": "fs_write"},
        color=False,
    )
    assert "frontend_developer/pasta" in line
    assert "·tool_used" in line and "tool=fs_write" in line


def test_fields_rendered_as_key_value() -> None:
    line = render_line(
        ts="12:00:00",
        tag="gaia",
        level="INFO",
        body="tool_used",
        fields={"tool": "fs_write", "dur": "5ms"},
        color=False,
    )
    assert "tool=fs_write" in line and "dur=5ms" in line


def test_error_tint_only_adds_color_when_enabled() -> None:
    # error=True changes the message colour; with colour off the layout is unchanged/plain.
    plain = render_line(ts="12:00:00", tag="x", level="INFO", body="boom", color=False, error=True)
    assert "\033[" not in plain
    colored = render_line(ts="12:00:00", tag="x", level="INFO", body="boom", color=True, error=True)
    assert "\033[" in colored


def test_error_fields_are_red() -> None:
    # A failed tool call (status=error) shows the command/args in red so the line reads as an error.
    red = render_line(
        ts="12:00:00",
        tag="gaia",
        level="INFO",
        body="",
        module="exec",
        fields={"command": "rm x"},
        color=True,
        error=True,
    )
    ok = render_line(
        ts="12:00:00",
        tag="gaia",
        level="INFO",
        body="",
        module="exec",
        fields={"command": "rm x"},
        color=True,
        error=False,
    )
    assert "\033[38;2;236;102;101m" in red  # error red fg around the fields
    assert "\033[38;2;236;102;101m" not in ok  # normal line: dim keys, no red


def test_multiline_body_indents_continuation() -> None:
    line = render_line(
        ts="12:00:00", tag="gaia", level="ERROR", body="oops\nTraceback line", color=False
    )
    assert "\n" in line
    cont = line.split("\n", 1)[1]
    assert cont.startswith(" ")  # the traceback line is indented under the body column

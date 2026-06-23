"""gocat-style console rendering for gaia's logs.

One renderer (:func:`render_line`) shared by the live logging formatter (:mod:`gaia.logs`) and
the ``gaia logs`` viewer (:mod:`gaia.cli.logs`) so the two never drift. A line is
``<ts>  <badge>  <tag>  ·<module>  <body>  <k=v …>``: a colour-per-actor tag, a colour-filled
level badge, a bold module (the tool/action or logger name), then default-white text. Truecolor
ANSI, degrading to the same plain layout when colour is off. Stdlib only and operates on plain
primitives (not ``LogRecord``) because the viewer renders raw JSON / text lines.
"""

from __future__ import annotations

import os
from typing import Any

#: Tag column width; longer tags truncate with ``…`` (gocat used 25 for bare Android tags).
TAG_WIDTH = 28
#: Continuation lines (wrapped text, tracebacks) indent to the body column.
_BODY_INDENT = " " * (10 + 3 + TAG_WIDTH + 2)  # "HH:MM:SS  " + badge " X " + tag + "  "

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_ITALIC = "\033[3m"

#: gocat's tag palette, picked by ``len(tag) % len(palette)`` so a name keeps its colour.
_TAG_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0xFF, 0xFF, 0xFF),
    (0xFF, 0x78, 0x5F),
    (0xFF, 0xD0, 0x63),
    (0x00, 0xDB, 0xB4),
    (0x00, 0xBC, 0xC4),
    (0x85, 0x6B, 0xF5),
    (0xCE, 0x8F, 0xF9),
    (0xFF, 0x65, 0xBF),
)

_WHITE = (0xFD, 0xF8, 0xDC)
_BLACK = (0x00, 0x00, 0x00)
_BLUE = (0x4A, 0xA6, 0xEF)
_GREEN = (0x5C, 0xD0, 0xA7)

#: level -> (letter, message rgb, badge fg, badge bg). INFO blue, DEBUG green (the rest gocat's).
_LEVELS: dict[str, tuple[str, tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "DEBUG": ("D", _GREEN, _BLACK, _GREEN),
    "INFO": ("I", _BLUE, _WHITE, _BLUE),
    "WARNING": ("W", (0xFF, 0xD8, 0x66), _BLACK, (0xFF, 0xD8, 0x66)),
    "ERROR": ("E", (0xEC, 0x66, 0x65), _WHITE, (0xEC, 0x66, 0x65)),
    "CRITICAL": ("F", (0xEF, 0x2D, 0x24), (0xEF, 0x2D, 0x24), _BLACK),
}
_DEFAULT_LEVEL = _LEVELS["INFO"]
_ERROR_RGB = _LEVELS["ERROR"][1]


def supports_color(stream: Any) -> bool:
    """True when ``stream`` is a tty that should get colour (honours ``NO_COLOR``/``TERM=dumb``)."""
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", None) and stream.isatty())


def _fg(rgb: tuple[int, int, int]) -> str:
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _bg(rgb: tuple[int, int, int]) -> str:
    return f"\033[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def tag_color(tag: str) -> tuple[int, int, int]:
    """The stable palette colour for ``tag`` (same name -> same colour)."""
    return _TAG_PALETTE[len(tag) % len(_TAG_PALETTE)]


def level_badge(level: str, *, color: bool) -> str:
    """The `` X `` level block: bold-italic letter on the level's colour (or the bare letter)."""
    letter, _msg, fg, bg = _LEVELS.get(level.upper(), _DEFAULT_LEVEL)
    if not color:
        return letter
    return f"{_bg(bg)}{_fg(fg)}{_BOLD}{_ITALIC} {letter} {_RESET}"


def _render_tag(tag: str, *, color: bool) -> str:
    """Right-align ``tag`` in :data:`TAG_WIDTH`; colour per-name, dimming any ``/project`` tail."""
    shown = tag if len(tag) <= TAG_WIDTH else tag[: TAG_WIDTH - 1] + "…"
    pad = " " * (TAG_WIDTH - len(shown))
    if not color:
        return pad + shown
    if "/" in shown:
        head, _, tail = shown.partition("/")
        return f"{pad}{_fg(tag_color(tag))}{head}{_RESET}{_DIM}/{tail}{_RESET}"
    return f"{pad}{_fg(tag_color(tag))}{shown}{_RESET}"


def _render_fields(fields: dict[str, Any], *, color: bool, error: bool = False) -> str:
    """``key=value …`` — dim keys normally, all-red on an error (plain when colour is off)."""
    plain = " ".join(f"{k}={v}" for k, v in fields.items())
    if not color:
        return plain
    if error:  # a failed call: show the command/args in red so the whole line reads as the error
        return f"{_fg(_ERROR_RGB)}{plain}{_RESET}"
    return " ".join(f"{_DIM}{k}={_RESET}{v}" for k, v in fields.items())


def render_line(
    *,
    ts: str,
    tag: str,
    level: str,
    body: str,
    module: str | None = None,
    fields: dict[str, Any] | None = None,
    color: bool,
    error: bool = False,
) -> str:
    """Render one log line. ``tag`` is the actor (shown every line), ``module`` the source
    (bold), ``body`` the message (default white). ``error`` (or an ERROR level) tints it red."""
    is_err = error or level.upper() in ("ERROR", "CRITICAL")
    time_col = f"{_DIM}{ts}{_RESET}" if color else ts
    tag_col = _render_tag(tag, color=color)

    def paint(text: str) -> str:
        return f"{_fg(_ERROR_RGB)}{text}{_RESET}" if color and text and is_err else text

    head, *rest = body.split("\n")
    segs: list[str] = []
    if module:
        mod_fg = _fg(_ERROR_RGB) if (color and is_err) else ""
        segs.append(f"·{mod_fg}{_BOLD}{module}{_RESET}" if color else f"·{module}")
    if head:
        segs.append(paint(head))
    if fields:
        segs.append(_render_fields(fields, color=color, error=is_err))

    line = f"{time_col}  {level_badge(level, color=color)}  {tag_col}  {'  '.join(segs)}".rstrip()
    for extra in rest:  # wrapped text / traceback lines align under the body column
        line = f"{line}\n{_BODY_INDENT}{paint(extra)}"
    return line


def demo() -> None:  # pragma: no cover - manual colour eyeball: ``python -m gaia.logfmt``
    """Print sample lines so the colours can be checked by eye (tests can't)."""
    import sys

    color = supports_color(sys.stdout)
    rows = [
        ("12:01:03", "frontend_developer/pasta", "INFO", "tool_used", {"tool": "fs_write"}),
        ("12:01:41", "gaia", "INFO", "delegate_to_soul", {"status": "success"}),
        ("12:02:10", "frontend_developer/pasta", "INFO", "tool_used", {"status": "error"}),
        ("12:02:14", "gaia", "WARNING", "connectors.whatsapp", None),
    ]
    for ts, tag, level, mod, fields in rows:
        err = bool(fields and fields.get("status") == "error")
        line = render_line(
            ts=ts, tag=tag, level=level, body="", module=mod, fields=fields, color=color, error=err
        )
        print(line)


if __name__ == "__main__":  # pragma: no cover
    demo()

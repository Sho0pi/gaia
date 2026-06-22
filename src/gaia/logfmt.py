"""gocat-style console rendering for gaia's logs (shared by the live formatter + ``gaia logs``).

Modelled on the user's Go project ``sho0pi/gocat`` (an ADB logcat viewer): a right-aligned,
colour-per-source **tag**, a colour-filled **level badge** (`` X ``), then the message in the
level's colour — consecutive lines from the same tag have their tag blanked so a run reads as one
block. Truecolor (24-bit) ANSI, degrading to a plain (uncoloured) but identically-aligned layout
when colour is off (``NO_COLOR``/``TERM=dumb``/not a tty).

Pure stdlib and import-light on purpose: both the logging ``ConsoleFormatter`` (live stdout) and
the ``gaia logs`` CLI viewer call :func:`render_line`, so the two surfaces never drift. Operates on
plain primitives, not ``LogRecord``, because the CLI renders raw JSON / text lines.
"""

from __future__ import annotations

import os
from typing import Any

#: Tag column width. Wide enough for ``agent/project`` and most ``gaia.*`` logger names; longer
#: tags truncate with ``…`` (gocat used 25 for bare Android tags).
TAG_WIDTH = 28
#: Continuation lines (wrapped messages, tracebacks) indent to the body column.
_BODY_INDENT = " " * (10 + 3 + TAG_WIDTH + 2)  # "HH:MM:SS  " + badge(" X ") + tag + "  "

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_ITALIC = "\033[3m"

#: gocat's tag palette (hex), picked by ``len(tag) % len(palette)`` so a name keeps its colour.
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

#: level -> (letter, foreground rgb [message colour], badge fg, badge bg). gocat's palette.
_LEVELS: dict[str, tuple[str, tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "DEBUG": ("D", (0x4A, 0xA6, 0xEF), _WHITE, (0x4A, 0xA6, 0xEF)),
    "INFO": ("I", (0x5C, 0xD0, 0xA7), _BLACK, (0x5C, 0xD0, 0xA7)),
    "WARNING": ("W", (0xFF, 0xD8, 0x66), _BLACK, (0xFF, 0xD8, 0x66)),
    "ERROR": ("E", (0xEC, 0x66, 0x65), _WHITE, (0xEC, 0x66, 0x65)),
    "CRITICAL": ("F", (0xEF, 0x2D, 0x24), (0xEF, 0x2D, 0x24), _BLACK),
}
_DEFAULT_LEVEL = _LEVELS["INFO"]
_ERROR_RGB = _LEVELS["ERROR"][1]


def supports_color(stream: Any) -> bool:
    """True when ``stream`` is an interactive tty that should get ANSI colour.

    Honours ``NO_COLOR`` and ``TERM=dumb`` so piped/redirected output (and tests) stay plain.
    """
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
    """The `` X `` level block — bold-italic letter on the level's colour (or plain ``X``)."""
    letter, _msg, fg, bg = _LEVELS.get(level.upper(), _DEFAULT_LEVEL)
    if not color:
        return letter
    return f"{_bg(bg)}{_fg(fg)}{_BOLD}{_ITALIC} {letter} {_RESET}"


def _render_tag(tag: str, *, color: bool) -> str:
    """Right-align ``tag`` in :data:`TAG_WIDTH`, truncate with ``…``, colour it per-tag.

    A ``parent/child`` tag (agent/project) keeps the parent in its colour and dims the ``/child``.
    """
    shown = tag if len(tag) <= TAG_WIDTH else tag[: TAG_WIDTH - 1] + "…"
    pad = " " * (TAG_WIDTH - len(shown))
    if not color:
        return pad + shown
    if "/" in shown:
        head, _, tail = shown.partition("/")
        body = f"{_fg(tag_color(tag))}{head}{_RESET}{_DIM}/{tail}{_RESET}"
    else:
        body = f"{_fg(tag_color(tag))}{shown}{_RESET}"
    return pad + body


def _render_fields(fields: dict[str, Any], *, color: bool) -> str:
    """``key=value …`` — dim keys, plain values (or plain text when colour is off)."""
    parts = []
    for k, v in fields.items():
        parts.append(f"{_DIM}{k}={_RESET}{v}" if color else f"{k}={v}")
    return " ".join(parts)


def render_line(
    *,
    ts: str,
    tag: str,
    level: str,
    body: str,
    fields: dict[str, Any] | None = None,
    color: bool,
    prev_tag: str | None = None,
    error: bool = False,
) -> str:
    """One gocat-style line: ``<ts>  <badge>  <tag>  <body>  <k=v …>``.

    ``tag`` is the source (logger name, or ``agent``/``agent/project`` for events); it's blanked
    when it equals ``prev_tag`` so a run from one source reads as a block. ``body`` is the message
    (system) or the bold action (events). ``error`` tints the body + fields red (event tool
    failures, whose level is still INFO). Continuation lines indent to the body column.
    """
    _letter, msg_rgb, _bfg, _bbg = _LEVELS.get(level.upper(), _DEFAULT_LEVEL)
    if error:
        msg_rgb = _ERROR_RGB
    time_col = f"{_DIM}{ts}{_RESET}" if color else ts
    badge = level_badge(level, color=color)
    tag_col = " " * TAG_WIDTH if tag == prev_tag else _render_tag(tag, color=color)

    body_lines = body.split("\n")
    head, *rest = body_lines
    if color and head:
        head = f"{_fg(msg_rgb)}{head}{_RESET}"
    line = f"{time_col}  {badge}  {tag_col}  {head}".rstrip()

    if fields:
        line = f"{line}  {_render_fields(fields, color=color)}"
    for extra in rest:  # wrapped message / traceback lines align under the body
        painted = f"{_fg(msg_rgb)}{extra}{_RESET}" if color and extra else extra
        line = f"{line}\n{_BODY_INDENT}{painted}"
    return line


def demo() -> None:  # pragma: no cover - manual eyeball
    """Print a few sample lines (``python -m gaia.logfmt``)."""
    color = supports_color_stdout()
    rows = [
        ("12:01:03", "frontend_developer/pasta-site", "INFO", "tool_used", {"tool": "fs_write"}),
        ("12:01:07", "frontend_developer/pasta-site", "INFO", "tool_used", {"tool": "serve"}),
        ("12:01:41", "gaia", "INFO", "delegate_to_soul", {"status": "success"}),
        ("12:02:10", "frontend_developer/pasta-site", "INFO", "tool_used", {"status": "error"}),
        ("12:02:14", "connectors.whatsapp", "WARNING", "reconnecting", None),
    ]
    prev = None
    for ts, tag, level, body, fields in rows:
        err = bool(fields and fields.get("status") == "error")
        line = render_line(
            ts=ts,
            tag=tag,
            level=level,
            body=body,
            fields=fields,
            color=color,
            prev_tag=prev,
            error=err,
        )
        print(line)
        prev = tag


def supports_color_stdout() -> bool:
    """Convenience: :func:`supports_color` for ``sys.stdout``."""
    import sys

    return supports_color(sys.stdout)


if __name__ == "__main__":  # pragma: no cover
    demo()

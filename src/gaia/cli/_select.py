"""A modern arrow-key single-select picker, shared by the setup wizard (and any CLI prompt).

prompt_toolkit-based (already a dep; same approach as the connectors picker and hermes-agent's
modals) — ↑/↓ to move, enter to choose, esc to cancel. Falls back to a numbered prompt when stdin
isn't a TTY (scripts, pipes, CI) so callers stay scriptable.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

#: One choice: (value, label, hint). ``hint`` is dimmed help text (may be "").
Option = tuple[str, str, str]


def select_one(title: str, options: Sequence[Option], *, default: str | None = None) -> str | None:
    """Pick one option's value via an arrow-key picker; ``None`` if cancelled.

    Non-TTY falls back to a numbered prompt (returns ``default`` on empty input).
    """
    opts = list(options)
    if not opts:
        return None
    if not sys.stdin.isatty():
        return _numbered(title, opts, default)
    return _picker(title, opts, default)


def _start_index(opts: Sequence[Option], default: str | None) -> int:
    return next((i for i, (value, _, _) in enumerate(opts) if value == default), 0)


def _picker(title: str, opts: Sequence[Option], default: str | None) -> str | None:
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    cursor = _start_index(opts, default)

    def render() -> StyleAndTextTuples:
        frags: StyleAndTextTuples = [
            ("bold", title),
            ("class:dim", "   ↑/↓ move · enter select · esc cancel\n"),
        ]
        for i, (_value, label, hint) in enumerate(opts):
            here = i == cursor
            pointer = "❯ " if here else "  "  # noqa: RUF001 - modern pointer glyph, intentional
            line = f"{pointer}{label}" + (f"   ({hint})" if hint else "")
            frags.append(("reverse" if here else "", line + "\n"))
        return frags

    kb = KeyBindings()
    app: Application[str | None]

    @kb.add("up")
    @kb.add("c-p")
    def _up(_e: object) -> None:
        nonlocal cursor
        cursor = (cursor - 1) % len(opts)
        app.invalidate()

    @kb.add("down")
    @kb.add("c-n")
    def _down(_e: object) -> None:
        nonlocal cursor
        cursor = (cursor + 1) % len(opts)
        app.invalidate()

    @kb.add("enter")
    def _enter(_e: object) -> None:
        app.exit(result=opts[cursor][0])

    @kb.add("escape")
    @kb.add("c-c")
    @kb.add("c-d")
    def _cancel(_e: object) -> None:
        app.exit(result=None)

    app = Application(
        layout=Layout(
            Window(FormattedTextControl(render, focusable=True), always_hide_cursor=True)
        ),
        key_bindings=kb,
        full_screen=False,
    )
    return app.run()


def _numbered(title: str, opts: Sequence[Option], default: str | None) -> str | None:
    """Numbered fallback for non-TTY: print the choices, read an index (default highlighted)."""
    import typer

    from gaia.cli._console import console

    out = console()
    out.print(f"[bold]{title}[/]")
    for i, (_value, label, hint) in enumerate(opts, 1):
        out.print(f"  {i}. {label}" + (f"  [dim]({hint})[/]" if hint else ""))
    start = _start_index(opts, default)
    raw = typer.prompt("Pick a number", default=str(start + 1)).strip()
    try:
        idx = int(raw) - 1
    except ValueError:
        return None
    return opts[idx][0] if 0 <= idx < len(opts) else None

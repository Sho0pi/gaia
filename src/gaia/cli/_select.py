"""Modern arrow-key pickers (single + multi select), themed in gaia's palette.

prompt_toolkit-based (already a dep; same approach as the connectors picker and hermes-agent's
modals). ↑/↓ move; enter chooses (select_one) / confirms (select_many); space toggles (select_many);
esc cancels. Options may carry a status ``badge``; multi-select supports group headers (an option
with an empty value) and a 3-state checkbox: `[ ]` untouched, `[-]` already configured, `[+]` picked
to change this run. Non-TTY falls back to a numbered prompt so callers stay scriptable.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

#: One choice: (value, label, hint[, badge]). An empty value marks a non-selectable group header.
Option = tuple[str, ...]

#: gaia-logo palette (docs brand.css): glow #22f0a8, moss #7bc79f, gold #d4b065, cream #efe7d2.
_THEME = {
    "title": "#22f0a8 bold",  # glow — the gaia accent, used for the header
    "help": "#5a8a72",  # mossy, muted — the key hints
    "bar": "#22f0a8 bold",  # the ▎ accent bar on the focused row
    "sel": "#f7f2e4 bold",  # focused label — bright cream
    "item": "#a8b5ac",  # unfocused label — soft sage
    "header": "#5a8a72 bold",  # group section headers
    "box_on": "#22f0a8 bold",  # [+] selected
    "box_mark": "#7bc79f",  # [-] already configured
    "box_off": "#3d4a40",  # [ ] empty, faint
    "moss": "#7bc79f",
    "gold": "#d4b065",
    "dim": "#5a8a72",
}


def _norm(options: Sequence[Option]) -> list[tuple[str, str, str, str]]:
    """Pad each option to (value, label, hint, badge)."""
    out: list[tuple[str, str, str, str]] = []
    for o in options:
        value, label = o[0], o[1]
        hint = o[2] if len(o) > 2 else ""
        badge = o[3] if len(o) > 3 else ""
        out.append((value, label, hint, badge))
    return out


def _badge_frags(badge: str) -> list[tuple[str, str]]:
    """Color a badge string's tokens: 'configured' → moss ✓, 'current' → gold (split on '·')."""
    frags: list[tuple[str, str]] = []
    for part in (p.strip() for p in badge.split("·")):
        if not part:
            continue
        low = part.lower()
        cls = "class:gold" if "current" in low else "class:moss"
        prefix = "✓ " if "config" in low else ""
        frags.append((cls, f"  {prefix}{part}"))
    return frags


def _pad_cell(glyph: str, *, width: int = 2) -> str:
    """Right-pad a marker glyph to a constant display width (handles wide/ambiguous glyphs)."""
    from prompt_toolkit.utils import get_cwidth

    return glyph + " " * max(1, width - get_cwidth(glyph) + 1)


def _row(opt: tuple[str, str, str, str], *, here: bool, state: str | None) -> list[tuple[str, str]]:
    """Fragments for one row. ``state`` None → single-select; "" value → a group header."""
    value, label, hint, badge = opt
    if not value:  # group header
        return [("class:header", f"\n  {label.upper()}\n")]
    frags: list[tuple[str, str]] = [("class:bar", "▎ ") if here else ("", "  ")]
    if state is not None:  # multi: 3-state marker, padded to a constant width so labels line up
        glyph, cls = {
            "selected": ("●", "class:box_on"),  # ticked to set up this run
            "marked": ("―", "class:box_mark"),  # already configured
            "none": ("○", "class:box_off"),  # not set up
        }[state]
        frags.append((cls, _pad_cell(glyph)))
    frags.append(("class:sel" if here else "class:item", label))
    frags.extend(_badge_frags(badge))
    if hint:
        frags.append(("class:dim", f"   {hint}"))
    frags.append(("", "\n"))
    return frags


def _selectable(opts: Sequence[tuple[str, str, str, str]]) -> list[int]:
    return [i for i, o in enumerate(opts) if o[0]]


def _start_index(opts: Sequence[tuple[str, str, str, str]], default: str | None) -> int:
    pick = _selectable(opts)
    return next((i for i in pick if opts[i][0] == default), pick[0] if pick else 0)


def _confirm_multi(selected: set[str], cursor_value: str) -> set[str]:
    """Confirmed multi-select values: the ticked ones, or — if none — the row under the cursor."""
    return selected if selected else {cursor_value}


def select_one(title: str, options: Sequence[Option], *, default: str | None = None) -> str | None:
    """Pick one value via the arrow-key picker; ``None`` if cancelled (non-TTY → numbered)."""
    opts = _norm(options)
    if not _selectable(opts):
        return None
    if not sys.stdin.isatty():
        picked = _numbered(title, opts, default=default, multi=False)
        return picked[0] if picked else None
    return _run(title, opts, default=default, multi=False)  # type: ignore[return-value]


def select_many(
    title: str,
    options: Sequence[Option],
    *,
    selected: Sequence[str] = (),
    marked: Sequence[str] = (),
) -> list[str]:
    """Multi-select: space toggles, enter confirms, esc cancels. Supports group headers + a 3-state
    checkbox — ``marked`` values show `[-]` (already configured), ``selected`` start ticked `[+]`.

    Enter with nothing ticked returns the single option under the cursor. Non-TTY → numbered prompt.
    """
    opts = _norm(options)
    if not _selectable(opts):
        return []
    if not sys.stdin.isatty():
        return _numbered(title, opts, marked=marked, multi=True)
    return _run(title, opts, selected=selected, marked=marked, multi=True)  # type: ignore[return-value]


def _run(
    title: str,
    opts: list[tuple[str, str, str, str]],
    *,
    default: str | None = None,
    selected: Sequence[str] = (),
    marked: Sequence[str] = (),
    multi: bool = False,
) -> str | list[str] | None:
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    pick = _selectable(opts)
    cursor = _start_index(opts, default)
    chosen: set[str] = set(selected)
    marked_set = set(marked)
    help_text = (
        "↑/↓ move   space select   enter confirm   esc cancel"
        if multi
        else "↑/↓ move   enter select   esc cancel"
    )

    def _state(value: str) -> str:
        if value in chosen:
            return "selected"
        return "marked" if value in marked_set else "none"

    def render() -> StyleAndTextTuples:
        frags: StyleAndTextTuples = [
            ("class:title", f"  {title}\n"),
            ("class:help", f"  {help_text}\n"),
        ]
        for i, opt in enumerate(opts):
            state = _state(opt[0]) if (multi and opt[0]) else None
            frags.extend(_row(opt, here=i == cursor, state=state))
        frags.append(("", "\n"))
        return frags

    kb = KeyBindings()
    app: Application[str | list[str] | None]

    def _step(delta: int) -> None:
        nonlocal cursor
        order = pick.index(cursor)
        cursor = pick[(order + delta) % len(pick)]
        app.invalidate()

    @kb.add("up")
    @kb.add("c-p")
    def _up(_e: object) -> None:
        _step(-1)

    @kb.add("down")
    @kb.add("c-n")
    def _down(_e: object) -> None:
        _step(1)

    if multi:

        @kb.add(" ")
        def _toggle(_e: object) -> None:
            chosen.symmetric_difference_update({opts[cursor][0]})
            app.invalidate()

        @kb.add("enter")
        def _confirm(_e: object) -> None:
            keep = _confirm_multi(chosen, opts[cursor][0])
            app.exit(result=[o[0] for o in opts if o[0] in keep])  # display order
    else:

        @kb.add("enter")
        def _choose(_e: object) -> None:
            app.exit(result=opts[cursor][0])

    @kb.add("escape")
    @kb.add("c-c")
    @kb.add("c-d")
    def _cancel(_e: object) -> None:
        app.exit(result=([] if multi else None))

    app = Application(
        layout=Layout(
            Window(FormattedTextControl(render, focusable=True), always_hide_cursor=True)
        ),
        key_bindings=kb,
        style=Style.from_dict(_THEME),
        full_screen=False,
    )
    return app.run()


def _numbered(
    title: str,
    opts: list[tuple[str, str, str, str]],
    *,
    default: str | None = None,
    marked: Sequence[str] = (),
    multi: bool = False,
) -> list[str]:
    """Numbered fallback for non-TTY. Single: one index. Multi: comma-separated indices."""
    import typer

    from gaia.cli._console import console

    out = console()
    out.print(f"[bold]{title}[/]")
    rows: list[tuple[str, str, str, str]] = []  # selectable only, numbered
    for value, label, hint, badge in opts:
        if not value:
            out.print(f"[bold]{label}[/]")  # group header
            continue
        rows.append((value, label, hint, badge))
        flags = "  ".join(p for p in (badge, "(configured)" if value in marked else "") if p)
        out.print(f"  {len(rows)}. {label}" + (f"  [dim]{flags}[/]" if flags else ""))
    if multi:
        raw = typer.prompt("Pick numbers (comma-separated)", default="").strip()
        picks = []
        for tok in raw.split(","):
            try:
                idx = int(tok) - 1
            except ValueError:
                continue
            if 0 <= idx < len(rows):
                picks.append(rows[idx][0])
        return picks
    start = next((i for i, r in enumerate(rows) if r[0] == default), 0)
    raw = typer.prompt("Pick a number", default=str(start + 1)).strip()
    try:
        idx = int(raw) - 1
    except ValueError:
        return []
    return [rows[idx][0]] if 0 <= idx < len(rows) else []

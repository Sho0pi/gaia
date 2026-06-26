"""Modern arrow-key pickers (single + multi select), themed in gaia's palette.

prompt_toolkit-based (already a dep; same approach as the connectors picker and hermes-agent's
modals). ↑/↓ move; enter chooses (select_one) / confirms (select_many); space toggles (select_many);
esc cancels. Each option may carry a status ``badge`` (e.g. "configured · current"). Falls back to a
numbered prompt when stdin isn't a TTY (scripts, pipes, CI) so callers stay scriptable.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

#: One choice: (value, label, hint[, badge]). ``hint`` is dim help; ``badge`` is a status string
#: ("configured", "current", or "a · b") whose tokens are colored (configured=moss, current=gold).
Option = tuple[str, ...]

#: gaia-logo palette (docs brand.css): glow accent, moss, gold, cream, muted.
_THEME = {
    "glow": "#22f0a8 bold",
    "moss": "#7bc79f",
    "gold": "#d4b065",
    "title": "#efe7d2 bold",
    "dim": "#6b7a6f",
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


def _row(
    value_label_hint_badge: tuple[str, str, str, str], *, here: bool, checked: bool | None
) -> list[tuple[str, str]]:
    """Fragments for one row. ``checked`` None → single-select (no checkbox)."""
    _value, label, hint, badge = value_label_hint_badge
    frags: list[tuple[str, str]] = [("class:glow" if here else "", "❯ " if here else "  ")]  # noqa: RUF001
    if checked is not None:
        frags.append(("class:glow" if checked else "class:dim", "[x] " if checked else "[ ] "))
    frags.append(("class:glow" if here else "", label))
    frags.extend(_badge_frags(badge))
    if hint:
        frags.append(("class:dim", f"  ({hint})"))
    frags.append(("", "\n"))
    return frags


def _start_index(opts: Sequence[tuple[str, str, str, str]], default: str | None) -> int:
    return next((i for i, o in enumerate(opts) if o[0] == default), 0)


def _resolve_multi(selected: set[int], cursor: int) -> list[int]:
    """Confirmed multi-select indices: the ticked ones, or — if none — the row under the cursor."""
    return sorted(selected) if selected else [cursor]


def select_one(title: str, options: Sequence[Option], *, default: str | None = None) -> str | None:
    """Pick one value via the arrow-key picker; ``None`` if cancelled (non-TTY → numbered)."""
    opts = _norm(options)
    if not opts:
        return None
    if not sys.stdin.isatty():
        picked = _numbered(title, opts, default=default, multi=False)
        return picked[0] if picked else None
    return _run(title, opts, default=default, multi=False)  # type: ignore[return-value]


def select_many(
    title: str, options: Sequence[Option], *, preselected: Sequence[str] = ()
) -> list[str]:
    """Multi-select picker: space toggles, enter confirms, esc cancels.

    Per spec: enter with nothing selected returns the single option under the cursor. Non-TTY →
    a comma-separated numbered prompt.
    """
    opts = _norm(options)
    if not opts:
        return []
    pre = [o[0] for o in opts if o[0] in set(preselected)]
    if not sys.stdin.isatty():
        return _numbered(title, opts, preselected=pre, multi=True)
    return _run(title, opts, preselected=pre, multi=True)  # type: ignore[return-value]


def _run(
    title: str,
    opts: list[tuple[str, str, str, str]],
    *,
    default: str | None = None,
    preselected: Sequence[str] = (),
    multi: bool = False,
) -> str | list[str] | None:
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    cursor = _start_index(opts, default)
    selected = {i for i, o in enumerate(opts) if o[0] in set(preselected)}
    help_text = (
        "   ↑/↓ move · space select · enter confirm · esc cancel\n"
        if multi
        else "   ↑/↓ move · enter select · esc cancel\n"
    )

    def render() -> StyleAndTextTuples:
        frags: StyleAndTextTuples = [("class:title", title), ("class:dim", help_text)]
        for i, opt in enumerate(opts):
            frags.extend(_row(opt, here=i == cursor, checked=(i in selected) if multi else None))
        return frags

    kb = KeyBindings()
    app: Application[str | list[str] | None]

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

    if multi:

        @kb.add(" ")
        def _toggle(_e: object) -> None:
            selected.symmetric_difference_update({cursor})
            app.invalidate()

        @kb.add("enter")
        def _confirm(_e: object) -> None:
            # Nothing ticked? take the row under the cursor (per spec).
            app.exit(result=[opts[i][0] for i in _resolve_multi(selected, cursor)])
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
    preselected: Sequence[str] = (),
    multi: bool = False,
) -> list[str]:
    """Numbered fallback for non-TTY. Single: one index. Multi: comma-separated indices."""
    import typer

    from gaia.cli._console import console

    out = console()
    out.print(f"[bold]{title}[/]")
    for i, (_value, label, hint, badge) in enumerate(opts, 1):
        extra = "  ".join(p for p in (badge, f"({hint})" if hint else "") if p)
        out.print(f"  {i}. {label}" + (f"  [dim]{extra}[/]" if extra else ""))
    if multi:
        raw = typer.prompt("Pick numbers (comma-separated)", default="").strip()
        if not raw:
            return []
        picks = []
        for tok in raw.split(","):
            try:
                idx = int(tok) - 1
            except ValueError:
                continue
            if 0 <= idx < len(opts):
                picks.append(opts[idx][0])
        return picks
    start = _start_index(opts, default)
    raw = typer.prompt("Pick a number", default=str(start + 1)).strip()
    try:
        idx = int(raw) - 1
    except ValueError:
        return []
    return [opts[idx][0]] if 0 <= idx < len(opts) else []

"""The gaia-palette help theme is applied to typer.rich_utils on CLI import."""

from __future__ import annotations


def test_help_theme_applied_on_cli_import() -> None:
    from typer import rich_utils as r

    import gaia.cli  # noqa: F401 — import applies the theme

    assert r.STYLE_OPTION == "bold #22f0a8"  # glow accent
    assert r.STYLE_COMMANDS_TABLE_FIRST_COLUMN == "bold #22f0a8"
    assert r.STYLE_COMMANDS_PANEL_BORDER == "#2b5c44"  # accent-green border


def test_apply_help_theme_is_idempotent() -> None:
    from typer import rich_utils as r

    from gaia.cli._help_theme import apply_help_theme

    apply_help_theme()
    apply_help_theme()
    assert r.STYLE_METAVAR == "#d4b065"  # gold, still set after a second call

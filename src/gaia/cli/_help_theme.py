"""Paint Typer/Click ``--help`` output in the gaia palette (docs ``brand.css``).

Typer renders help via Rich using module-level style constants in ``typer.rich_utils``. We override
them once at CLI import so every command's help matches the interactive pickers (see ``_select``):
glow ``#22f0a8`` accents, moss/gold/cream/sage. Presentation-only, same spirit as the ``_console``
singleton exception to the no-module-state rule.
"""

from __future__ import annotations

# gaia-logo palette
_GLOW = "#22f0a8"
_MOSS = "#7bc79f"
_GOLD = "#d4b065"
_CREAM = "#efe7d2"
_SAGE = "#a8b5ac"
_MUTED = "#5a8a72"
_ACCENT = "#2b5c44"
_WARN = "#d98c7a"


def apply_help_theme() -> None:
    """Override ``typer.rich_utils`` styles with the gaia palette (idempotent)."""
    from typer import rich_utils as r

    r.STYLE_USAGE = f"bold {_GLOW}"
    r.STYLE_USAGE_COMMAND = f"bold {_CREAM}"
    r.STYLE_HELPTEXT_FIRST_LINE = _CREAM
    r.STYLE_HELPTEXT = _SAGE
    r.STYLE_OPTION = f"bold {_GLOW}"
    r.STYLE_SWITCH = _MOSS
    r.STYLE_NEGATIVE_OPTION = _WARN
    r.STYLE_NEGATIVE_SWITCH = _WARN
    r.STYLE_METAVAR = _GOLD
    r.STYLE_METAVAR_SEPARATOR = _MUTED
    r.STYLE_OPTION_HELP = _SAGE
    r.STYLE_OPTION_DEFAULT = _MUTED
    r.STYLE_OPTION_ENVVAR = _GOLD
    r.STYLE_REQUIRED_SHORT = _WARN
    r.STYLE_REQUIRED_LONG = _WARN
    r.STYLE_COMMANDS_TABLE_FIRST_COLUMN = f"bold {_GLOW}"
    r.STYLE_OPTIONS_PANEL_BORDER = _ACCENT
    r.STYLE_COMMANDS_PANEL_BORDER = _ACCENT
    r.STYLE_ERRORS_PANEL_BORDER = _WARN
    r.STYLE_ERRORS_SUGGESTION = _MOSS
    r.STYLE_ABORTED = _WARN

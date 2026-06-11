"""Generate a commented default ``god.yaml`` directly from the schema.

Rather than maintain a second hand-written copy of the config (which drifts the
moment a field is added), the default file is rendered by walking
:class:`~godpy.config.schema.GodConfig`'s fields — emitting each field's default value
prefixed by its ``description`` as a ``#`` comment. Add a field to ``schema.py`` and
the scaffold picks it up for free. ``test_scaffold`` round-trips the output back
through ``GodConfig`` to guarantee it stays valid and in sync.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from godpy.config.schema import GodConfig

_HEADER = """\
# god.yaml — godpy runtime config (non-secret, hot-reloaded).
# Edit and save; changes are picked up without a restart.
# Secrets (tokens, api keys) belong in env / .env, NOT here.
"""

#: A string safe to emit as a bare YAML scalar: starts alphanumeric, then word chars /
#: space / dot / dash / slash. Anything else (e.g. '@playwright/mcp@latest') is quoted.
_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9][\w .\-/]*$")
#: Bare words YAML would read as a bool/null rather than the literal string.
_YAML_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "none", "~"}


def _yaml_str(value: str) -> str:
    """Render a string as a bare scalar when safe, else a double-quoted one."""
    if value and _PLAIN_SAFE.match(value) and value.lower() not in _YAML_RESERVED:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _scalar(value: object) -> str:
    """Render a leaf default as a YAML scalar."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[]" if not value else repr(value)
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, Path):
        return f'"{value}"' if str(value) else '""'
    if isinstance(value, str):
        return _yaml_str(value)
    return str(value)


def _render_instance(model: BaseModel, indent: int) -> list[str]:
    """Render a model *instance* so each field shows its real default value.

    Walking the instance (not the class) matters when a field's default differs from
    its type's class default — e.g. ``vector_store`` defaults to a ``MemoryProvider``
    whose ``provider`` is ``chroma``, not the class default ``gemini``.
    """
    pad = "  " * indent
    lines: list[str] = []
    for name, field in type(model).model_fields.items():
        if field.description:
            lines.append(f"{pad}# {field.description}")
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            lines.append(f"{pad}{name}:")
            lines.extend(_render_instance(value, indent + 1))
        else:
            lines.append(f"{pad}{name}: {_scalar(value)}")
    return lines


def render_default_yaml() -> str:
    """Render the commented default ``god.yaml`` from the live schema."""
    return _HEADER + "\n" + "\n".join(_render_instance(GodConfig(), 0)) + "\n"


def write_default_config(path: Path, *, override: bool = False) -> bool:
    """Write the generated default to ``path``.

    Skips an existing file unless ``override=True``. Returns True if written.
    """
    path = Path(path)
    if path.exists() and not override:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_default_yaml())
    return True

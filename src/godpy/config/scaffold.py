"""Generate a commented default ``god.yaml`` directly from the schema.

Rather than maintain a second hand-written copy of the config (which drifts the
moment a field is added), the default file is rendered by walking
:class:`~godpy.config.schema.GodConfig`'s fields — emitting each field's default value
prefixed by its ``description`` as a ``#`` comment. Add a field to ``schema.py`` and
the scaffold picks it up for free. ``test_scaffold`` round-trips the output back
through ``GodConfig`` to guarantee it stays valid and in sync.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Union, get_args, get_origin

from pydantic import BaseModel

from godpy.config.schema import GodConfig

_HEADER = """\
# god.yaml — godpy runtime config (non-secret, hot-reloaded).
# Edit and save; changes are picked up without a restart.
# Secrets (tokens, api keys) belong in env / .env, NOT here.
"""


def _unwrap_optional(annotation: object) -> object:
    """``X | None`` -> ``X``; leave anything else untouched."""
    if get_origin(annotation) in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _is_model(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


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
    return str(value)


def _render_model(model_cls: type[BaseModel], indent: int) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    for name, field in model_cls.model_fields.items():
        if field.description:
            lines.append(f"{pad}# {field.description}")
        annotation = _unwrap_optional(field.annotation)
        if _is_model(annotation):
            lines.append(f"{pad}{name}:")
            lines.extend(_render_model(annotation, indent + 1))  # type: ignore[arg-type]
        else:
            default = field.get_default(call_default_factory=True)
            lines.append(f"{pad}{name}: {_scalar(default)}")
    return lines


def render_default_yaml() -> str:
    """Render the commented default ``god.yaml`` from the live schema."""
    return _HEADER + "\n" + "\n".join(_render_model(GodConfig, 0)) + "\n"


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

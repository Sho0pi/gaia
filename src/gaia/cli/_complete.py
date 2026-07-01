"""Shell-completion callbacks for the gaia CLI (wired via Typer ``autocompletion=``).

Advanced *value* completion on top of Typer's free command/option completion: config keys and
their allowed values, soul keys, task ids, user refs, tool ids, capabilities, channels, styles,
providers. Every callback is **best-effort** - any failure or missing source returns ``[]`` so
tab-completion never crashes or hangs the user's shell. Heavy sources are lazy-imported *inside*
each callback, keeping this module's import cost at typer + stdlib (the CLI lazy-import rule).

Callbacks return either a ``list[str]`` or a ``list[tuple[value, help]]`` (Typer renders the help).
"""

from __future__ import annotations

from typing import Any

# --- config keys + values (walk the pydantic schema) --------------------------------------


def _walk_config() -> list[tuple[str, str]]:
    """Every dotted config key with a short type label, from the ``GaiaConfig`` schema."""
    from pydantic import BaseModel

    from gaia.config.schema import GaiaConfig

    out: list[tuple[str, str]] = []

    def walk(model: type[BaseModel], prefix: str) -> None:
        for name, field in model.model_fields.items():
            dotted = f"{prefix}{name}"
            ann = field.annotation
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                out.append((dotted, "section"))
                walk(ann, dotted + ".")
            else:
                out.append((dotted, _type_label(ann)))

    walk(GaiaConfig, "")
    return out


def _type_label(ann: Any) -> str:
    """A short, human hint for a field's type (for the completion help column)."""
    import typing

    origin = typing.get_origin(ann)
    if origin is typing.Literal:
        return "|".join(str(a) for a in typing.get_args(ann))
    return getattr(ann, "__name__", str(ann)).replace("NoneType", "none")


def _field_annotation(dotted: str) -> Any:
    """The annotation of the field at ``dotted``, or ``None`` if the path doesn't resolve."""
    from pydantic import BaseModel

    from gaia.config.schema import GaiaConfig

    model: Any = GaiaConfig
    ann: Any = None
    for part in dotted.split("."):
        fields = getattr(model, "model_fields", None)
        if not fields or part not in fields:
            return None
        ann = fields[part].annotation
        model = ann if isinstance(ann, type) and issubclass(ann, BaseModel) else None
    return ann


def _allowed_values(ann: Any) -> list[str]:
    """The completable values for a field: bool → true/false, Literal/enum → its members."""
    import enum
    import typing

    if ann is bool:
        return ["true", "false"]
    if typing.get_origin(ann) is typing.Literal:
        return [str(a) for a in typing.get_args(ann)]
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        return [str(m.value) for m in ann]
    return []


def config_keys(incomplete: str) -> list[tuple[str, str]]:
    """Complete a dotted config key (`gaia config get|set <TAB>`)."""
    try:
        return [(k, label) for k, label in _walk_config() if k.startswith(incomplete)]
    except Exception:
        return []


def config_values(ctx: Any, incomplete: str) -> list[str]:
    """Complete the value for the config key already on the line (bool / Literal / enum fields).

    ``ctx`` is a Click/Typer Context (annotated ``Any`` so this module needs no runtime typer
    import - Typer injects it by parameter name).
    """
    try:
        key = ctx.params.get("key")
        if not isinstance(key, str):
            return []
        return [v for v in _allowed_values(_field_annotation(key)) if v.startswith(incomplete)]
    except Exception:
        return []


# --- runtime stores (souls / tasks / users) -----------------------------------------------


def soul_keys(incomplete: str) -> list[tuple[str, str]]:
    """Complete a soul key from the registry."""
    try:
        from gaia.agents.registry import SoulRegistry
        from gaia.config import get_settings

        reg = SoulRegistry(get_settings(None).agent_registry_dir)
        return [(k, "soul") for k in reg.list_keys() if k.startswith(incomplete)]
    except Exception:
        return []


def task_ids(incomplete: str) -> list[tuple[str, str]]:
    """Complete a task id (with its title as help)."""
    try:
        from gaia.missions.store import TaskStore

        return [
            (t.id, t.title or t.status.value)
            for t in TaskStore().list()
            if t.id.startswith(incomplete)
        ]
    except Exception:
        return []


def user_refs(incomplete: str) -> list[tuple[str, str]]:
    """Complete a user ref (canonical id, with role + name as help)."""
    try:
        from gaia.users import UserStore

        hits = []
        for u in UserStore().list():
            if u.id.startswith(incomplete):
                hits.append((u.id, f"{u.role}{f' · {u.name}' if u.name else ''}"))
        return hits
    except Exception:
        return []


# --- static vocabularies ------------------------------------------------------------------

#: Tool ids that appear as `tools.<id>` config / `--tool` pins. Best-effort for completion; a
#: missing entry only means one fewer suggestion, never a wrong command.
_TOOL_IDS = (
    "web_fetch", "web_search", "remember", "load_memory", "cron", "download_media", "ask_user",
    "generate_image", "send_file", "set_communication_style", "run_command", "save_skill",
    "task_create", "task_get", "task_list", "task_update", "task_complete",
    "browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_screenshot",
    "browser_scroll", "browser_press", "browser_back", "browser_get_images", "browser_console",
    "browser_dialog", "browser_evaluate",
    "fs_read", "fs_write", "fs_edit", "fs_glob", "fs_grep",
    "exec", "exec_poll", "exec_kill", "exec_list",
)  # fmt: skip


def tool_ids(incomplete: str) -> list[str]:
    """Complete a tool id."""
    return [t for t in _TOOL_IDS if t.startswith(incomplete)]


def capabilities(incomplete: str) -> list[str]:
    """Complete an ACL capability group name."""
    try:
        from gaia.acl import GROUPS

        return [g for g in GROUPS if g.startswith(incomplete)]
    except Exception:
        return []


def statuses(incomplete: str) -> list[str]:
    """Complete a task status."""
    try:
        from gaia.missions.store import TaskStatus

        return [s.value for s in TaskStatus if s.value.startswith(incomplete)]
    except Exception:
        return []


def _static(*values: str) -> Any:
    """Build a prefix-filtering completer over a fixed vocabulary."""

    def complete(incomplete: str) -> list[str]:
        return [v for v in values if v.startswith(incomplete)]

    return complete


channels = _static("telegram", "whatsapp", "cli")
styles = _static("human", "caveman", "ai")
providers = _static("gemini", "openai", "anthropic", "openrouter")
roles = _static("admin", "user", "guest")

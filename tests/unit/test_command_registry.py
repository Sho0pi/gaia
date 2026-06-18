"""CommandRegistry resolution + default_registry enabled-flag gating."""

from __future__ import annotations

from gaia.commands import default_registry
from gaia.commands.registry import _is_enabled
from gaia.config import CommandConfig, GaiaConfig


def test_default_registry_has_every_builtin() -> None:
    names = {c.name for c in default_registry().all()}

    assert names == {
        "help",
        "reset",
        "whoami",
        "agents",
        "status",
        "remember",
        "memories",
        "forget",
        "users",
        "approve",
        "remove",
        "name",
        "link",
        "tasks",
        "grant",
        "revoke",
        "perms",
        "acl",
        "skill",
    }


def test_aliases_resolve_to_their_command() -> None:
    registry = default_registry()

    assert registry.get("clear").name == "reset"
    assert registry.get("new").name == "reset"
    assert registry.get("stats").name == "status"
    assert registry.get("memory").name == "memories"


def test_lookup_is_case_insensitive_and_unknown_is_none() -> None:
    registry = default_registry()

    assert registry.get("HELP").name == "help"
    assert registry.get("nope") is None


def test_all_is_distinct_and_sorted() -> None:
    names = [c.name for c in default_registry().all()]

    assert names == sorted(names)
    assert len(names) == len(set(names))  # aliases don't duplicate the command


def test_disabled_command_is_dropped() -> None:
    config = GaiaConfig(commands={"forget": CommandConfig(enabled=False)})

    registry = default_registry(config)

    assert registry.get("forget") is None
    assert registry.get("help") is not None  # others unaffected


def test_is_enabled_defaults_true() -> None:
    assert _is_enabled(None, "forget") is True
    assert _is_enabled(GaiaConfig(), "forget") is True
    assert (
        _is_enabled(GaiaConfig(commands={"forget": CommandConfig(enabled=False)}), "forget")
        is False
    )

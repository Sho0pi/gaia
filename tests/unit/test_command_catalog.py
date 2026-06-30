"""The command catalog (single source) + the formatted, role-filtered /help."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from gaia.commands import CommandContext, default_registry
from gaia.commands.catalog import CATEGORY_ORDER, info


def _registry() -> object:
    return default_registry(None)


def _ctx(reg: object, *, args: str = "", role: str = "admin") -> CommandContext:
    # No stored user record → authorize falls back to the context role (admin = full, user = gated).
    gaia = SimpleNamespace(users=SimpleNamespace(get=lambda _u: None), config=None)
    return CommandContext(
        args=args,
        gaia=gaia,
        handler=None,
        registry=reg,
        user_id="u",
        session_id="s",
        role=role,  # type: ignore[arg-type]
    )


def test_every_registered_command_has_a_catalog_entry() -> None:
    reg = _registry()
    missing = [c.name for c in reg.all() if info(c.name) is None]
    assert not missing, f"commands without a catalog entry: {missing}"


def test_catalog_categories_are_known() -> None:
    # every entry's category is one of the ordered sections (so none silently falls to "Other")
    for entry in (info(c.name) for c in _registry().all()):
        assert entry is not None and entry.category in CATEGORY_ORDER


def test_help_list_is_grouped_and_role_filtered() -> None:
    reg = _registry()
    h = reg.get("help")

    admin = asyncio.run(h.run(_ctx(reg, role="admin")))
    user = asyncio.run(h.run(_ctx(reg, role="user")))

    assert "*Chat & memory*" in admin and "*Admin*" in admin  # bold section headers
    assert "/model" in admin  # admin sees admin commands
    assert "/model" not in user and "/grant" not in user  # a regular user does not
    assert "/help <command> for details" in user  # the tip


def test_help_command_detail_has_usage_and_examples() -> None:
    reg = _registry()
    out = asyncio.run(reg.get("help").run(_ctx(reg, args="skill")))

    assert "*/skill" in out and "<list|show|search|install|remove>" in out  # bold head + usage
    assert "*Examples*" in out and "/skill install" in out


def test_help_unknown_gives_a_friendly_hint() -> None:
    reg = _registry()

    sub = asyncio.run(reg.get("help").run(_ctx(reg, args="approve")))
    bogus = asyncio.run(reg.get("help").run(_ctx(reg, args="zzznope")))

    assert "*/approve" in sub  # 'approve' is a real command → its help (not the hint)
    assert "No command /zzznope" in bogus  # a truly unknown name → the friendly hint

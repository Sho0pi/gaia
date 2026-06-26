"""`TelegramConnector` — setMyCommands registration (#62)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("telegram")

from gaia.connectors.telegram import TelegramConnector


def _fake_app() -> Any:
    """A stand-in Application whose bot records set_my_commands calls."""
    calls: list[Any] = []

    async def set_my_commands(menu: Any) -> None:
        calls.append(menu)

    return SimpleNamespace(bot=SimpleNamespace(set_my_commands=set_my_commands, calls=calls))


async def _noop_dispatch(*_a: Any, **_k: Any) -> None:  # pragma: no cover
    return None


async def test_register_commands_sends_valid_menu() -> None:
    conn = TelegramConnector(
        "tok", _noop_dispatch, commands=[("help", "Show help"), ("reset", "X")]
    )
    app = _fake_app()

    await conn._register_commands(app)

    sent = app.bot.calls[0]
    assert [(c.command, c.description) for c in sent] == [("help", "Show help"), ("reset", "X")]


async def test_register_commands_skips_non_conforming_names() -> None:
    conn = TelegramConnector("tok", _noop_dispatch, commands=[("bad-name", "x"), ("ok", "y")])
    app = _fake_app()

    await conn._register_commands(app)

    assert [c.command for c in app.bot.calls[0]] == ["ok"]  # dashed name dropped


async def test_register_commands_caps_description_at_256() -> None:
    conn = TelegramConnector("tok", _noop_dispatch, commands=[("help", "z" * 300)])
    app = _fake_app()

    await conn._register_commands(app)

    assert len(app.bot.calls[0][0].description) == 256


async def test_register_commands_noop_without_commands() -> None:
    conn = TelegramConnector("tok", _noop_dispatch)  # no commands passed
    app = _fake_app()

    await conn._register_commands(app)

    assert app.bot.calls == []  # set_my_commands never called

"""Dispatcher: resolve sender → user, gate guests, route to a per-user handler.

``gaia.core.handler.build_handler`` is monkeypatched to a recording fake so we exercise
the routing/gating without ADK or a model backend; the user store is real (tmp file).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import gaia.core.dispatch as dispatch_mod
from gaia.connectors.base import Inbound
from gaia.core.dispatch import Dispatcher
from gaia.users import UserStore


class _FakeHandler:
    def __init__(self, user_id: str, session_id: str, role: str) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.role = role
        self.calls: list[str] = []
        self.flushed = 0

    async def __call__(self, inbound: Inbound, send: Any) -> None:
        self.calls.append(inbound.text)
        await send(f"handled:{inbound.text}")

    async def flush(self) -> None:
        self.flushed += 1


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_FakeHandler]:
    """Record every handler the dispatcher builds (monkeypatched build_handler)."""
    handlers: list[_FakeHandler] = []

    def fake_build(
        _gaia: Any, *, user_id: str, session_id: str, role: str = "admin"
    ) -> _FakeHandler:
        h = _FakeHandler(user_id, session_id, role)
        handlers.append(h)
        return h

    monkeypatch.setattr(dispatch_mod, "build_handler", fake_build)
    return handlers


def _gaia(tmp_path: Path) -> Any:
    """A minimal Gaia stand-in: a real UserStore + connector default_role config."""
    connectors = SimpleNamespace(
        whatsapp=SimpleNamespace(default_role="guest"),
        telegram=SimpleNamespace(default_role="guest"),
        cli=SimpleNamespace(default_role="admin"),
    )
    return SimpleNamespace(
        users=UserStore(tmp_path / "users.json"),
        config=SimpleNamespace(connectors=connectors),
    )


async def _send_collect(out: list[str]) -> Any:
    async def send(reply: Any) -> None:
        out.append(reply)

    return send


async def test_unknown_remote_sender_is_gated_as_guest(
    tmp_path: Path, built: list[_FakeHandler]
) -> None:
    gaia = _gaia(tmp_path)
    gaia.users.register("whatsapp", "owner@s.whatsapp.net", "Owner", "admin")  # admin exists
    d = Dispatcher(gaia)
    out: list[str] = []

    await d.for_channel("whatsapp")(
        "972@s.whatsapp.net", "Grace", Inbound(text="hi"), await _send_collect(out)
    )

    assert built == []  # no handler built — never reached the model
    assert out == []  # guests get silence on the wire; approval is out-of-band
    # but the sender is now a pending guest the admin can see
    user = gaia.users.resolve("whatsapp", "972@s.whatsapp.net")
    assert user is not None and user.role == "guest" and user.name == "Grace"


async def test_first_contact_becomes_admin_when_no_admin_exists(
    tmp_path: Path, built: list[_FakeHandler]
) -> None:
    # Fresh instance, no admin yet → the first remote sender (the owner) is bootstrapped as admin.
    gaia = _gaia(tmp_path)
    d = Dispatcher(gaia)
    out: list[str] = []

    await d.for_channel("whatsapp")(
        "972@s.whatsapp.net", "Itay", Inbound(text="hi"), await _send_collect(out)
    )

    user = gaia.users.resolve("whatsapp", "972@s.whatsapp.net")
    assert user is not None and user.role == "admin"  # bootstrapped
    assert built  # reached the model (not gated)


async def test_approved_user_routes_to_per_user_handler(
    tmp_path: Path, built: list[_FakeHandler]
) -> None:
    gaia = _gaia(tmp_path)
    gaia.users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")
    d = Dispatcher(gaia)
    out: list[str] = []

    await d.for_channel("whatsapp")(
        "972@s.whatsapp.net", "Grace", Inbound(text="hi"), await _send_collect(out)
    )

    assert out == ["handled:hi"]
    assert len(built) == 1
    assert built[0].user_id == "grace"  # memory partitions by canonical id
    assert built[0].session_id == "grace:whatsapp" and built[0].role == "user"


async def test_handler_cached_per_user_channel(tmp_path: Path, built: list[_FakeHandler]) -> None:
    gaia = _gaia(tmp_path)
    gaia.users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")
    d = Dispatcher(gaia)
    out: list[str] = []
    send = await _send_collect(out)

    wa = d.for_channel("whatsapp")
    await wa("972@s.whatsapp.net", "Grace", Inbound(text="one"), send)
    await wa("972@s.whatsapp.net", "Grace", Inbound(text="two"), send)

    assert len(built) == 1  # same handler reused
    assert built[0].calls == ["one", "two"]


async def test_same_person_two_channels_shares_user_id(
    tmp_path: Path, built: list[_FakeHandler]
) -> None:
    gaia = _gaia(tmp_path)
    itay = gaia.users.register("whatsapp", "111@s.whatsapp.net", "Itay", role="admin")
    gaia.users.link(itay.id, "telegram", "42")
    d = Dispatcher(gaia)
    out: list[str] = []
    send = await _send_collect(out)

    await d.for_channel("whatsapp")("111@s.whatsapp.net", "Itay", Inbound(text="a"), send)
    await d.for_channel("telegram")("42", "Itay", Inbound(text="b"), send)

    # two handlers (one per channel session) but the SAME memory partition (user_id)
    assert {h.user_id for h in built} == {"itay"}
    assert {h.session_id for h in built} == {"itay:whatsapp", "itay:telegram"}


async def test_cli_local_is_trusted_admin(tmp_path: Path, built: list[_FakeHandler]) -> None:
    gaia = _gaia(tmp_path)
    d = Dispatcher(gaia)
    out: list[str] = []

    await d.for_channel("cli")("local", "operator", Inbound(text="hi"), await _send_collect(out))

    assert out == ["handled:hi"]  # not gated
    assert built[0].role == "admin"  # cli default_role


async def test_cli_is_admin_even_if_config_says_otherwise(
    tmp_path: Path, built: list[_FakeHandler]
) -> None:
    # The local operator owns the machine — a mis-set connectors.cli.default_role
    # must never lock them out. cli is always admin, config notwithstanding.
    gaia = _gaia(tmp_path)
    gaia.config.connectors.cli.default_role = "guest"  # someone fat-fingers the config
    d = Dispatcher(gaia)
    out: list[str] = []

    await d.for_channel("cli")("local", "operator", Inbound(text="hi"), await _send_collect(out))

    assert out == ["handled:hi"]  # still not gated
    assert built[0].role == "admin"


async def test_flush_all_drains_every_handler(tmp_path: Path, built: list[_FakeHandler]) -> None:
    gaia = _gaia(tmp_path)
    gaia.users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")
    d = Dispatcher(gaia)
    await d.for_channel("whatsapp")(
        "972@s.whatsapp.net", "Grace", Inbound(text="hi"), await _send_collect([])
    )

    await d.flush_all()

    assert built[0].flushed == 1

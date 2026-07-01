"""UserStore: resolve / register (slug + dedup) / role / name / link / seed_admins."""

from __future__ import annotations

import json
from pathlib import Path

from gaia.users import UserStore, normalize_wa_number


def _store(tmp_path: Path) -> UserStore:
    return UserStore(tmp_path / "users.json")


def test_normalize_wa_number_is_forgiving() -> None:
    jid = "972501234567@s.whatsapp.net"
    assert normalize_wa_number("+972 50-123-4567") == jid
    assert normalize_wa_number("972501234567") == jid
    assert normalize_wa_number("(972) 50 123 4567") == jid
    assert normalize_wa_number(jid) == jid  # a full jid passes through
    assert normalize_wa_number("  no digits  ") is None


def test_register_and_resolve_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    assert user.id == "grace"
    assert user.role == "user"
    resolved = store.resolve("whatsapp", "972@s.whatsapp.net")
    assert resolved is not None and resolved.id == "grace"
    assert store.resolve("whatsapp", "unknown") is None


def test_register_slug_dedupes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.register("telegram", "1", "Grace", role="user")
    b = store.register("whatsapp", "2@s.whatsapp.net", "Grace", role="user")

    assert a.id == "grace"
    assert b.id == "grace-2"  # same display name, distinct canonical ids


def test_register_falls_back_to_qualified_id_without_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.register("telegram", "12345", "", role="guest")

    assert user.id == "telegram-12345"


def test_set_role_and_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "972@s.whatsapp.net", "g", role="guest")

    assert store.set_role("g", "user").role == "user"  # type: ignore[union-attr]
    assert store.set_name("g", "Grace").name == "Grace"  # type: ignore[union-attr]
    assert store.set_role("nobody", "admin") is None


def test_link_moves_identity_to_one_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    itay = store.register("whatsapp", "111@s.whatsapp.net", "Itay", role="admin")
    store.register("telegram", "999", "Itay TG", role="user")  # a stray duplicate person

    linked = store.link(itay.id, "telegram", "999")

    assert linked is not None
    assert "telegram:999" in linked.identities
    # the identity now resolves to itay, not the stray
    assert store.resolve("telegram", "999").id == "itay"  # type: ignore[union-attr]


def test_seed_admins_creates_and_promotes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # pre-existing non-admin that the seed should promote in place
    store.register("whatsapp", "111@s.whatsapp.net", "Itay", role="user")

    store.seed_admins(["whatsapp:111@s.whatsapp.net", "telegram:42"])

    assert store.resolve("whatsapp", "111@s.whatsapp.net").role == "admin"  # type: ignore[union-attr]
    seeded = store.resolve("telegram", "42")
    assert seeded is not None and seeded.role == "admin"


def test_seed_admins_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.seed_admins(["telegram:42"])
    store.seed_admins(["telegram:42"])  # again

    admins = [u for u in store.list() if u.role == "admin"]
    assert len(admins) == 1  # not duplicated


def test_write_is_atomic_json(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    store = UserStore(path)
    store.register("cli", "local", "Op", role="admin")

    data = json.loads(path.read_text())
    assert data[0]["id"] == "op" and data[0]["role"] == "admin"
    assert not path.with_suffix(".json.tmp").exists()  # tmp cleaned up by rename


def test_concurrent_registers_dont_lose_writes(tmp_path: Path) -> None:
    # The store is a shared singleton hit from connector threads; without locking the
    # read-modify-write, racing first-contact registers drop updates (last _write wins).
    import threading

    store = UserStore(tmp_path / "users.json")

    def reg(i: int) -> None:
        store.register("telegram", str(i), f"u{i}", "user")

    threads = [threading.Thread(target=reg, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store.list()) == 20  # every register persisted, none lost

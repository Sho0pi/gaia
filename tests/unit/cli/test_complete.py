"""gaia CLI shell-completion callbacks (``cli/_complete``): dynamic completion, crash-proof."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.cli import _complete


def test_config_keys_walks_the_schema() -> None:
    keys = [k for k, _ in _complete.config_keys("")]
    assert "llm.model" in keys
    assert "connectors.whatsapp.allow" in keys  # nested section walked
    assert all(k.startswith("missions.") for k, _ in _complete.config_keys("missions."))


def test_config_values_for_a_bool_field() -> None:
    ctx = SimpleNamespace(params={"key": "connectors.whatsapp.enabled"})
    assert set(_complete.config_values(ctx, "")) == {"true", "false"}
    assert _complete.config_values(ctx, "t") == ["true"]


def test_config_values_plain_string_field_is_empty() -> None:
    ctx = SimpleNamespace(params={"key": "llm.model"})  # a free str — nothing to enumerate
    assert _complete.config_values(ctx, "") == []


def test_static_vocabs_prefix_filter() -> None:
    assert _complete.channels("wh") == ["whatsapp"]
    assert _complete.roles("") == ["admin", "user", "guest"]
    assert _complete.styles("c") == ["caveman"]
    assert _complete.providers("") == ["gemini", "openai"]
    assert "browser_click" in _complete.tool_ids("browser_c")


def test_statuses_and_capabilities_read_their_source() -> None:
    assert "running" in _complete.statuses("")
    assert "web" in _complete.capabilities("")


def test_user_refs_reads_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    users = tmp_path / "users.json"
    monkeypatch.setattr("gaia.constants.USERS_FILE", users)
    from gaia.users import UserStore

    UserStore(users).register("whatsapp", "972@s.whatsapp.net", "Grace", "user")
    assert "grace" in [r for r, _ in _complete.user_refs("")]


def test_store_completers_never_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point stores at empty/absent locations — a failing source must yield [] not crash the shell.
    monkeypatch.setattr("gaia.constants.USERS_FILE", tmp_path / "absent.json")
    assert _complete.user_refs("") == []
    assert isinstance(_complete.soul_keys(""), list)
    assert isinstance(_complete.task_ids(""), list)

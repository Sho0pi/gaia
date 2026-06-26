"""First-run acceptance gate (gaia.legal): record/check round-trip + the ensure_accepted paths.

The autouse home isolation points HOME_DIR at a tmp dir, so each test starts unaccepted. conftest
sets GAIA_ACCEPT_TERMS globally; tests that exercise the prompt/refuse paths delete it.
"""

from __future__ import annotations

import json

import pytest

from gaia import legal


def test_record_then_is_accepted() -> None:
    assert legal.is_accepted() is False
    legal.record_acceptance()
    assert legal.is_accepted() is True
    data = json.loads(legal.accepted_path().read_text())
    assert (
        data["version"] == legal.ACCEPTANCE_VERSION and data["accepted_at"] and data["gaia_version"]
    )


def test_old_version_re_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    legal.accepted_path().parent.mkdir(parents=True, exist_ok=True)
    legal.accepted_path().write_text(json.dumps({"version": 0}))  # below current
    assert legal.is_accepted() is False


def test_env_var_records_and_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAIA_ACCEPT_TERMS", "1")
    legal.ensure_accepted()  # no TTY needed
    assert legal.is_accepted() is True


def test_non_tty_without_env_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GAIA_ACCEPT_TERMS", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit):
        legal.ensure_accepted()
    assert legal.is_accepted() is False


def test_tty_accept_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GAIA_ACCEPT_TERMS", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "accept")
    legal.ensure_accepted()
    assert legal.is_accepted() is True


def test_tty_decline_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GAIA_ACCEPT_TERMS", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "no")
    with pytest.raises(SystemExit):
        legal.ensure_accepted()
    assert legal.is_accepted() is False


def test_already_accepted_is_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    legal.record_acceptance()
    monkeypatch.delenv("GAIA_ACCEPT_TERMS", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # would refuse if it reached here
    legal.ensure_accepted()  # returns immediately, no exit

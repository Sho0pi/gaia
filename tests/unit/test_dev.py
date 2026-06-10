"""Dev web mode: agent loader, run_dev wiring, and main dispatch."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from godpy.dev import make_agent_loader


def test_loader_serves_god_root_once() -> None:
    built: list[int] = []
    root = object()

    def build() -> object:
        built.append(1)
        return root

    loader = make_agent_loader(SimpleNamespace(build_root_agent=build))

    assert loader.list_agents() == ["god"]
    assert loader.load_agent("god") is root
    assert loader.load_agent("god") is root
    assert len(built) == 1  # built once, then cached


def test_run_dev_builds_god_and_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    fake_god = SimpleNamespace(config=SimpleNamespace(logging=None))
    monkeypatch.setattr("godpy.app.God", lambda settings: fake_god)
    monkeypatch.setattr("godpy.app.setup_logging", lambda *a, **k: None)

    import godpy.dev as devmod

    monkeypatch.setattr(
        devmod,
        "serve_dev",
        lambda god, *, host, port: captured.update(god=god, host=host, port=port),
    )

    from godpy.app import run_dev

    run_dev(settings=SimpleNamespace(), host="0.0.0.0", port=9001)

    assert captured == {"god": fake_god, "host": "0.0.0.0", "port": 9001}


def test_main_dispatches_dev_with_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    called: dict[str, Any] = {}
    monkeypatch.setattr(main, "run_dev", lambda **kwargs: called.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["main.py", "dev", "--port", "9001"])

    main.main()

    assert called == {"env_file": None, "host": "127.0.0.1", "port": 9001}

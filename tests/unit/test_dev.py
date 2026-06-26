"""Dev web mode: agent loader and run_dev wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gaia.dev import make_agent_loader


def test_loader_serves_gaia_root_once() -> None:
    built: list[int] = []
    root = object()

    def build() -> object:
        built.append(1)
        return root

    loader = make_agent_loader(SimpleNamespace(build_root_agent=build))

    assert loader.list_agents() == ["gaia"]
    assert loader.load_agent("gaia") is root
    assert loader.load_agent("gaia") is root
    assert len(built) == 1  # built once, then cached


def test_run_dev_builds_gaia_and_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    fake_gaia = SimpleNamespace(config=SimpleNamespace(logging=None))
    monkeypatch.setattr("gaia.app.Gaia", lambda settings: fake_gaia)
    monkeypatch.setattr("gaia.app.setup_logging", lambda *a, **k: None)
    monkeypatch.setattr("gaia.app.write_default_config", lambda path: None)  # tested elsewhere

    import gaia.dev as devmod

    monkeypatch.setattr(
        devmod,
        "serve_dev",
        lambda gaia, *, host, port: captured.update(gaia=gaia, host=host, port=port),
    )

    from gaia.app import run_dev

    run_dev(settings=SimpleNamespace(config_path="x"), host="0.0.0.0", port=9001)

    assert captured == {"gaia": fake_gaia, "host": "0.0.0.0", "port": 9001}

"""Unit tests for ConfigStore — the hot-swappable god.yaml supplier."""

from __future__ import annotations

import os
from pathlib import Path

from godpy.config import ConfigStore, GodConfig, Settings, write_default_config


def _write(path: Path, model: str) -> None:
    path.write_text(f"llm:\n  model: {model}\n")


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "god.yaml", Settings())

    assert store.current.llm.model == "gemini-2.0-flash"


def test_current_reflects_written_file(tmp_path: Path) -> None:
    path = tmp_path / "god.yaml"
    _write(path, "gemini-3.1-flash-lite")

    store = ConfigStore(path, Settings())

    assert store.current.llm.model == "gemini-3.1-flash-lite"


def test_hot_swap_on_mtime_change(tmp_path: Path) -> None:
    path = tmp_path / "god.yaml"
    _write(path, "model-a")
    store = ConfigStore(path, Settings())
    assert store.current.llm.model == "model-a"

    _write(path, "model-b")
    # Force a distinct mtime so the change is detected regardless of fs granularity.
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))

    assert store.current.llm.model == "model-b"


def test_subscribe_fires_on_reload(tmp_path: Path) -> None:
    path = tmp_path / "god.yaml"
    _write(path, "model-a")
    store = ConfigStore(path, Settings())

    seen: list[str] = []
    store.subscribe(lambda cfg: seen.append(cfg.llm.model))

    _write(path, "model-b")
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))

    _ = store.current  # triggers the reload
    assert seen == ["model-b"]


def test_env_token_overrides_file(tmp_path: Path) -> None:
    path = tmp_path / "god.yaml"
    path.write_text("connectors:\n  telegram:\n    enabled: true\n    token: from-file\n")
    settings = Settings(telegram_bot_token="from-env")

    store = ConfigStore(path, settings)

    assert store.current.connectors.telegram.token == "from-env"


def test_scaffold_writes_only_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "god.yaml"

    assert write_default_config(path) is True
    assert path.exists()
    # Re-validates as a real GodConfig and does not clobber on second call.
    GodConfig.model_validate({})  # sanity: defaults construct
    assert write_default_config(path) is False

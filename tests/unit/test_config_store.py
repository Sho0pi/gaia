"""Unit tests for ConfigSupplier — the hot-swappable gaia.yaml supplier."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from gaia.config import ConfigSupplier, GaiaConfig, render_default_yaml, write_default_config


def _write(path: Path, model: str) -> None:
    path.write_text(f"llm:\n  model: {model}\n")


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    supplier = ConfigSupplier(tmp_path / "gaia.yaml")

    assert supplier.current.llm.model == "gemini-2.0-flash"


def test_current_reflects_written_file(tmp_path: Path) -> None:
    path = tmp_path / "gaia.yaml"
    _write(path, "gemini-3.1-flash-lite")

    supplier = ConfigSupplier(path)

    assert supplier.current.llm.model == "gemini-3.1-flash-lite"


def test_hot_swap_on_mtime_change(tmp_path: Path) -> None:
    path = tmp_path / "gaia.yaml"
    _write(path, "model-a")
    supplier = ConfigSupplier(path)
    assert supplier.current.llm.model == "model-a"

    _write(path, "model-b")
    # Force a distinct mtime so the change is detected regardless of fs granularity.
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))

    assert supplier.current.llm.model == "model-b"


def test_scaffold_respects_override_flag(tmp_path: Path) -> None:
    path = tmp_path / "gaia.yaml"

    assert write_default_config(path) is True
    assert path.exists()
    # Default: never clobber an existing (possibly edited) file.
    assert write_default_config(path) is False
    # override=True force-rewrites it.
    assert write_default_config(path, override=True) is True


def test_generated_default_is_valid_and_in_sync() -> None:
    # The schema-generated default must parse and equal a pristine GaiaConfig — this is
    # what guarantees the scaffold never drifts from schema.py.
    loaded = yaml.safe_load(render_default_yaml())

    assert GaiaConfig.model_validate(loaded) == GaiaConfig()

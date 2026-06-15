"""Comment-preserving config edits: targeted flips that keep the scaffold readable."""

from __future__ import annotations

from pathlib import Path

import yaml as pyyaml

from gaia.cli._yamledit import set_config_value


def test_flips_value_and_preserves_comments(tmp_path: Path) -> None:
    path = tmp_path / "gaia.yaml"
    path.write_text(
        "connectors:\n"
        "  telegram:\n"
        "    # Run the Telegram connector.\n"
        "    enabled: false\n"
        "  whatsapp:\n"
        "    enabled: false\n"
    )

    set_config_value(path, "connectors.telegram.enabled", True)

    text = path.read_text()
    assert "# Run the Telegram connector." in text  # comment survived the rewrite
    data = pyyaml.safe_load(text)
    assert data["connectors"]["telegram"]["enabled"] is True
    assert data["connectors"]["whatsapp"]["enabled"] is False  # untouched


def test_missing_file_scaffolds_then_edits(tmp_path: Path) -> None:
    path = tmp_path / "gaia.yaml"

    set_config_value(path, "connectors.telegram.enabled", True)

    text = path.read_text()
    assert pyyaml.safe_load(text)["connectors"]["telegram"]["enabled"] is True
    assert "#" in text  # the commented scaffold was generated, not a bare two-liner


def test_creates_missing_intermediate_mappings(tmp_path: Path) -> None:
    path = tmp_path / "gaia.yaml"
    path.write_text("llm:\n  provider: gemini\n")

    set_config_value(path, "connectors.telegram.enabled", True)

    data = pyyaml.safe_load(path.read_text())
    assert data["connectors"]["telegram"]["enabled"] is True
    assert data["llm"]["provider"] == "gemini"

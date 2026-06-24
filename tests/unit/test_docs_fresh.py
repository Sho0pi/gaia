"""The generated docs Reference pages must match `scripts/gen_reference.py` output.

Fails if a command / config key / tool changed without regenerating, or if a generated page was
hand-edited. Fix: `uv run python scripts/gen_reference.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_REF = _ROOT / "docs" / "src" / "content" / "docs" / "reference"


def _gen() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location(
        "gen_reference", _ROOT / "scripts" / "gen_reference.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        "cli.md": module.gen_cli(),
        "commands.md": module.gen_commands(),
        "config.md": module.gen_config(),
        "tools.md": module.gen_tools(),
    }


@pytest.mark.parametrize("name", ["cli.md", "commands.md", "config.md", "tools.md"])
def test_reference_page_is_fresh(name: str) -> None:
    expected = _gen()[name]
    actual = (_REF / name).read_text()
    assert actual == expected, (
        f"docs reference {name} is stale — run `uv run python scripts/gen_reference.py`"
    )

"""System test: load a real gaia.yaml from disk and drive the launch policy.

Pure end-to-end of the config path (write file -> ConfigSupplier -> plan_launch); no
model backend, network, or native deps required.
"""

from __future__ import annotations

from pathlib import Path

from gaia.app import plan_launch
from gaia.config import ConfigSupplier

_YAML = """\
llm:
  model: gemini-3.1-flash-lite
connectors:
  whatsapp:
    enabled: true
    allow: ['123456789']
  telegram:
    enabled: true
  cli:
    enabled: false
"""


def test_real_yaml_drives_connector_launch(tmp_path: Path) -> None:
    path = tmp_path / "gaia.yaml"
    path.write_text(_YAML)

    supplier = ConfigSupplier(path)
    config = supplier.current

    assert config.llm.model == "gemini-3.1-flash-lite"
    assert config.connectors.whatsapp.allow == ["123456789"]
    assert plan_launch(config) == ["whatsapp", "telegram"]

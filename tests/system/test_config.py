"""System test: load a real god.yaml from disk and drive the launch policy.

Pure end-to-end of the config path (write file -> ConfigStore -> plan_launch); no
model backend, network, or native deps required.
"""

from __future__ import annotations

from pathlib import Path

from godpy.app import plan_launch
from godpy.config import ConfigStore, Settings

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
    path = tmp_path / "god.yaml"
    path.write_text(_YAML)

    store = ConfigStore(path, Settings())
    config = store.current

    assert config.llm.model == "gemini-3.1-flash-lite"
    assert config.connectors.whatsapp.allow == ["123456789"]
    assert plan_launch(config) == ["whatsapp", "telegram"]

"""Comment-preserving ``gaia.yaml`` edits (the seed of the #98 config group).

The default config file is generated with a comment for every field
(``config/scaffold.py``); a plain ``yaml.dump`` rewrite would destroy them all on the
first edit. ``ruamel.yaml``'s round-trip mode parses and re-emits the document with
comments and formatting intact, so a targeted ``set_config_value`` changes exactly one
value and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def set_config_value(path: Path, dotted_key: str, value: Any) -> None:
    """Set ``dotted_key`` (e.g. ``connectors.telegram.enabled``) in ``path``, in place.

    A missing file is scaffolded first (``write_default_config``) so the edit lands in
    the commented default rather than a bare two-line document. Intermediate mappings
    are created as needed.
    """
    from ruamel.yaml import YAML

    from gaia.config import write_default_config

    write_default_config(path)  # no-op when the file exists
    yaml = YAML()  # round-trip mode: comments/format preserved
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text()) or {}

    node = data
    *parents, leaf = dotted_key.split(".")
    for part in parents:
        if part not in node or node[part] is None:
            node[part] = {}
        node = node[part]
    node[leaf] = value

    with path.open("w") as fh:
        yaml.dump(data, fh)

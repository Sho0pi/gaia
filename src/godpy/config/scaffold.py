"""Write a commented default ``god.yaml`` so first-run users have a starting point.

PyYAML's dumper drops comments, so the template is a hand-written string rather than
``yaml.dump(GodConfig().model_dump())``. It documents the shape and the
secrets-stay-in-env rule. Only written when the file is missing — never clobbers an
edited config.
"""

from __future__ import annotations

from pathlib import Path

_DEFAULT_CONFIG = """\
# god.yaml — godpy runtime config (non-secret, hot-reloaded).
# Edit and save; running godpy picks up changes without a restart.
# Secrets (tokens, api keys) belong in env / .env, NOT here.

llm:
  provider: gemini
  model: gemini-2.0-flash

# Sender ids with admin privileges (reserved; not yet enforced).
admin: []

connectors:
  whatsapp:
    enabled: false
    store_path: ""            # empty = ~/.godpy/whatsapp.db (default)
    allow: []                 # empty = allow everyone
    group_trigger:
      mention_only: true
    default_soul: god
    default_role: user
  cli:
    enabled: true             # local terminal chat; foreground-exclusive
    default_soul: god
    default_role: admin
  telegram:
    enabled: false
    # token comes from env GODPY_TELEGRAM_BOT_TOKEN — leave blank here.

# --- Forward-looking sections: validated but not yet wired into the runtime. ---
roles: {}
tools: {}
souls: {}
"""


def write_default_config(path: Path) -> bool:
    """Write the commented default to ``path`` if absent. Returns True if written."""
    path = Path(path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_CONFIG)
    return True

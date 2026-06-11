"""Single source of the project's identity.

Everything that encodes the name "godpy" — the home directory, default file
locations, logger names, the env-var prefix — derives from :data:`APP_NAME` here, so
a runtime rename is a one-line change. (Renaming the Python *package* ``godpy`` itself
is a separate mechanical step.)

Module-cohesive constants (prompts, redaction patterns, …) deliberately stay in their
own modules — only cross-cutting *identity* lives here.
"""

from __future__ import annotations

from pathlib import Path

# The one knob. Change this to rebrand every path / logger / env prefix below.
APP_NAME = "godpy"

# All runtime state lives under a single dotted home directory (e.g. ~/.godpy).
HOME_DIR = Path.home() / f".{APP_NAME}"

# Secrets file (env vars). Read by Settings; override per-run with --env-file.
ENV_FILE = HOME_DIR / ".env"

# Prefix for app-owned environment variables (e.g. GODPY_CONFIG). Third-party names
# like GEMINI_API_KEY are not derived from this.
ENV_PREFIX = f"{APP_NAME.upper()}_"

# Default file/dir locations, all under HOME_DIR.
CONFIG_PATH = HOME_DIR / "god.yaml"  # "god.yaml" names the God agent, not the package
LOG_DIR = HOME_DIR / "logs"
SKILLS_DIR = HOME_DIR / "skills"
SESSION_DB = HOME_DIR / "whatsapp.db"
AGENT_REGISTRY_DIR = HOME_DIR / "agent_registry"
# Pidfile for the background daemon (godpy start/stop/status).
PID_FILE = HOME_DIR / f"{APP_NAME}.pid"
# Per-agent state. Each agent's sandboxed filesystem workspace lives at
# AGENTS_DIR / <agent_name> / "workspace" (see tools/filesystem.py).
AGENTS_DIR = HOME_DIR / "agents"

# Logging identity. The system logger is the app name; events are a child of it.
LOGGER_NAME = APP_NAME
EVENTS_LOGGER_NAME = f"{APP_NAME}.events"

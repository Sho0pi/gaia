"""Single source of the project's identity.

Everything that encodes the name "gaia" — the home directory, default file
locations, logger names, the env-var prefix — derives from :data:`APP_NAME` here, so
a runtime rename is a one-line change. (Renaming the Python *package* ``gaia`` itself
is a separate mechanical step.)

Module-cohesive constants (prompts, redaction patterns, …) deliberately stay in their
own modules — only cross-cutting *identity* lives here.
"""

from __future__ import annotations

from pathlib import Path

# The one knob. Change this to rebrand every path / logger / env prefix below.
APP_NAME = "gaia"

# All runtime state lives under a single dotted home directory (e.g. ~/.gaia).
HOME_DIR = Path.home() / f".{APP_NAME}"

# Secrets file (env vars). Read by Settings; override per-run with --env-file.
ENV_FILE = HOME_DIR / ".env"

# Prefix for app-owned environment variables (e.g. GAIA_CONFIG). Third-party names
# like GEMINI_API_KEY are not derived from this.
ENV_PREFIX = f"{APP_NAME.upper()}_"

# Default file/dir locations, all under HOME_DIR.
CONFIG_PATH = HOME_DIR / "gaia.yaml"  # "gaia.yaml" names the Gaia agent, not the package
LOG_DIR = HOME_DIR / "logs"
# Redacted crash reports (one JSON per fatal daemon failure) — surfaced by `gaia report`.
CRASHES_DIR = HOME_DIR / "crashes"
SKILLS_DIR = HOME_DIR / "skills"
SESSION_DB = HOME_DIR / "whatsapp.db"
# ADK conversation sessions (durable, sliding-window) — survive restarts; idle-consolidated to mem0.
SESSIONS_DB = HOME_DIR / "sessions.db"
AGENT_REGISTRY_DIR = HOME_DIR / "agent_registry"
# Pidfile for the background daemon (gaia start/stop/status).
PID_FILE = HOME_DIR / f"{APP_NAME}.pid"
# Unix socket for local CLI clients attaching to the daemon.
SOCKET_FILE = HOME_DIR / f"{APP_NAME}.sock"
# Scheduled jobs (the cron store; managed by the cron tool / `gaia cron`).
CRON_FILE = HOME_DIR / "cron.json"
# Known users: (channel, sender) -> canonical user identity + role (the user store).
USERS_FILE = HOME_DIR / "users.json"
# Missions task board (SQLite, WAL); managed by the task_* tools / `gaia task`.
TASKS_DB = HOME_DIR / "tasks.db"
# Per-agent state. Each agent's sandboxed filesystem workspace lives at
# AGENTS_DIR / <agent_name> / "workspace" (see tools/filesystem.py).
AGENTS_DIR = HOME_DIR / "agents"
# Transient downloaded artifacts (e.g. inbound voice notes under cache/voice/).
CACHE_DIR = HOME_DIR / "cache"
# Files a user sent in (e.g. an inbound image). A read root in every agent's sandbox so a
# tool/soul can read or copy an uploaded file (e.g. embed it in a website), not just see it.
UPLOADS_DIR = HOME_DIR / "uploads"

# Logging identity. The system logger is the app name; events are a child of it.
LOGGER_NAME = APP_NAME
EVENTS_LOGGER_NAME = f"{APP_NAME}.events"

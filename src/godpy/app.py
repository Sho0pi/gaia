"""Application launcher: build God, read god.yaml, launch the enabled connectors.

Which connectors run is driven by ``god.yaml`` (:class:`~godpy.config.GodConfig`),
not by which credentials happen to be present. The WhatsApp *backend* (business vs
regular/QR) is still auto-picked from creds by :func:`select_connector`.

Launch rules (:func:`plan_launch`, pure + unit-testable):

* The CLI/Textual TUI is a **foreground** app — it cannot share the event loop with
  background connectors, so enabling it alongside another connector is rejected.
* The remaining connectors are async and co-run via :func:`asyncio.gather`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from godpy.config import GodConfig, Settings, get_settings, write_default_config
from godpy.connectors import (
    CLIConnector,
    TelegramConnector,
    WhatsAppConnector,
    WhatsAppWebConnector,
)
from godpy.connectors.base import Handler
from godpy.god import God
from godpy.god.handler import build_handler
from godpy.logs import setup_logging

logger = logging.getLogger(__name__)


def select_connector(
    settings: Settings, handler: Handler
) -> WhatsAppConnector | WhatsAppWebConnector:
    """Choose the WhatsApp backend from configured credentials."""
    if settings.has_whatsapp_business:
        assert settings.whatsapp_phone_id and settings.whatsapp_token  # narrowed by property
        return WhatsAppConnector(settings.whatsapp_phone_id, settings.whatsapp_token, handler)
    return WhatsAppWebConnector(settings.whatsapp_session_db, handler)


def plan_launch(config: GodConfig) -> list[str]:
    """Return the names of connectors to launch, or raise on an invalid combo.

    Pure: no I/O, so the policy (CLI-exclusivity, enabled set) is unit-testable.
    """
    connectors = config.connectors
    background = [
        name
        for name, conf in (("whatsapp", connectors.whatsapp), ("telegram", connectors.telegram))
        if conf.enabled
    ]
    if connectors.cli.enabled:
        if background:
            raise ValueError(
                "The CLI connector is foreground-exclusive and cannot run alongside "
                f"other connectors (also enabled: {', '.join(background)}). "
                "Disable the others or disable cli in god.yaml."
            )
        return ["cli"]
    return background


def run_cli(settings: Settings | None = None, *, env_file: Path | None = None) -> None:
    """Launch the local CLI/TUI frontend and chat with God in the terminal."""
    settings = settings or get_settings(env_file)
    god = God(settings)
    setup_logging(settings, god.config.logging)
    CLIConnector(build_handler(god)).run()


def run_auth(provider: str, *, env_file: Path | None = None) -> None:
    """Run an interactive provider login and store the credentials.

    Currently supports ``openai`` (Sign in with ChatGPT, device-code flow).
    """
    settings = get_settings(env_file)
    god = God(settings)
    setup_logging(settings, god.config.logging)
    if provider in ("openai", "openai-chatgpt", "chatgpt"):
        from godpy.providers.openai import login

        creds = asyncio.run(login())
        creds.save()
        logger.info("ChatGPT credentials saved for account %s", creds.account_id)
    else:
        raise SystemExit(f"unknown auth provider: {provider!r} (try: openai)")


def run(settings: Settings | None = None, *, env_file: Path | None = None) -> None:
    """Build God and launch the connectors enabled in god.yaml."""
    settings = settings or get_settings(env_file)
    write_default_config(settings.config_path)
    god = God(settings)
    setup_logging(settings, god.config.logging)
    selected = plan_launch(god.config)

    if not selected:
        logger.warning("no connectors enabled in god.yaml — nothing to run")
        return

    if selected == ["cli"]:
        CLIConnector(build_handler(god)).run()
        return

    asyncio.run(_run_background(settings, god, selected))


async def _run_background(settings: Settings, god: God, selected: list[str]) -> None:
    """Run the enabled async connectors concurrently until interrupted."""
    handler = build_handler(god)
    tasks: list[asyncio.Task[None]] = []

    if "whatsapp" in selected:
        connector = select_connector(settings, handler)
        if isinstance(connector, WhatsAppWebConnector):
            tasks.append(asyncio.create_task(connector.start()))
        else:
            # Business backend delivers inbound over an HTTP webhook that isn't wired
            # yet (issue #3). Build the client so config is validated, but say so loudly.
            connector.build_client()
            logger.warning(
                "whatsapp business backend selected, but the inbound webhook server is "
                "not wired yet (see issue #3) — no messages will be received"
            )

    if "telegram" in selected:
        token = god.config.connectors.telegram.token
        if not token:
            logger.warning(
                "telegram enabled but no token (set GODPY_TELEGRAM_BOT_TOKEN) — skipping"
            )
        else:
            tasks.append(asyncio.create_task(TelegramConnector(token, handler).start()))

    if not tasks:
        return
    try:
        await asyncio.gather(*tasks)
    finally:
        # Drain any turns still buffered for memory before the process exits, so a
        # Ctrl-C doesn't drop the tail of the conversation (best-effort).
        flush = getattr(handler, "flush", None)
        if flush is not None:
            await flush()

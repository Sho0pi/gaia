"""Application launcher: build God, pick a WhatsApp backend, run it.

Backend selection is automatic and pure (:func:`select_connector`) so it can be
unit-tested without any network or native deps:

* Cloud-API (pywa) creds present  -> :class:`WhatsAppConnector` (business).
* otherwise                        -> :class:`WhatsAppWebConnector` (regular, QR).
"""

from __future__ import annotations

import asyncio

from godpy.config import Settings, get_settings
from godpy.connectors import CLIConnector, WhatsAppConnector, WhatsAppWebConnector
from godpy.connectors.base import Handler
from godpy.god import God
from godpy.god.handler import build_handler


def select_connector(
    settings: Settings, handler: Handler
) -> WhatsAppConnector | WhatsAppWebConnector:
    """Choose the WhatsApp backend from configured credentials."""
    if settings.has_whatsapp_business:
        assert settings.whatsapp_phone_id and settings.whatsapp_token  # narrowed by property
        return WhatsAppConnector(settings.whatsapp_phone_id, settings.whatsapp_token, handler)
    return WhatsAppWebConnector(settings.whatsapp_session_db, handler)


def run_cli(settings: Settings | None = None) -> None:
    """Launch the local CLI/TUI frontend and chat with God in the terminal."""
    settings = settings or get_settings()
    god = God(settings)
    CLIConnector(build_handler(god)).run()


def run(settings: Settings | None = None) -> None:
    """Launch the bot and connect it to the selected WhatsApp backend."""
    settings = settings or get_settings()
    god = God(settings)
    handler = build_handler(god)
    connector = select_connector(settings, handler)

    if isinstance(connector, WhatsAppWebConnector):
        asyncio.run(connector.start())
        return

    # Business backend: pywa delivers inbound messages over an HTTP webhook, which
    # needs a public server that isn't wired yet (tracked in issue #3). Build the
    # client so config is validated, but make the gap loud rather than silent.
    connector.build_client()
    print(
        "[whatsapp] business backend selected, but the inbound webhook server is "
        "not wired yet (see issue #3) — no messages will be received."
    )

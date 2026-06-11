"""I/O adapters for chat platforms. Dumb pipes only — no agent logic here.

A connector receives an inbound message, hands it to a ``handler`` coroutine,
and sends the reply back. All reasoning lives in :mod:`gaia.core`.
"""

from gaia.connectors.cli import CLIConnector
from gaia.connectors.telegram import TelegramConnector
from gaia.connectors.whatsapp import WhatsAppConnector
from gaia.connectors.whatsapp_web import WhatsAppWebConnector

__all__ = ["CLIConnector", "TelegramConnector", "WhatsAppConnector", "WhatsAppWebConnector"]

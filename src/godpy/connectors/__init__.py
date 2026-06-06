"""I/O adapters for chat platforms. Dumb pipes only — no agent logic here.

A connector receives an inbound message, hands it to a ``handler`` coroutine,
and sends the reply back. All reasoning lives in :mod:`godpy.god`.
"""

from godpy.connectors.telegram import TelegramConnector
from godpy.connectors.whatsapp import WhatsAppConnector

__all__ = ["TelegramConnector", "WhatsAppConnector"]

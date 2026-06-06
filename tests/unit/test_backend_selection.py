"""Unit tests for WhatsApp backend auto-selection (pure, no network/native deps)."""

from __future__ import annotations

from pathlib import Path

from godpy.app import select_connector
from godpy.config import Settings
from godpy.connectors import WhatsAppConnector, WhatsAppWebConnector
from godpy.connectors.base import Send


async def _handler(_text: str, _send: Send) -> None:  # pragma: no cover - never invoked here
    return None


def test_business_creds_select_pywa_connector() -> None:
    settings = Settings(whatsapp_phone_id="phone-123", whatsapp_token="tok-abc")

    connector = select_connector(settings, _handler)

    assert isinstance(connector, WhatsAppConnector)


def test_no_creds_select_neonize_connector(tmp_path: Path) -> None:
    db = tmp_path / "whatsapp.db"
    settings = Settings(whatsapp_session_db=db)

    connector = select_connector(settings, _handler)

    assert isinstance(connector, WhatsAppWebConnector)
    assert connector._session_db == db


def test_partial_business_creds_fall_back_to_neonize() -> None:
    # Only one of the two business fields set -> not enough for the business backend.
    settings = Settings(whatsapp_phone_id="phone-123")

    assert isinstance(select_connector(settings, _handler), WhatsAppWebConnector)

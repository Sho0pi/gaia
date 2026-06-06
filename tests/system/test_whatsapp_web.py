"""System test: the neonize connector builds a real client against the live lib.

Two guards keep CI green: skip if neonize isn't installed, and skip the live
QR-pairing path unless ``GODPY_WHATSAPP_RUN_LIVE`` is set (it needs a real phone
to scan the code, which can't run unattended).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from godpy.connectors import WhatsAppWebConnector
from godpy.connectors._neonize_compat import patch_protobuf_version_guard

patch_protobuf_version_guard()  # same protobuf<7 guard the connector applies
neonize = pytest.importorskip("neonize.aioze.client", reason="needs the neonize native lib")


async def _handler(text: str) -> str:  # pragma: no cover - not exercised offline
    return text


def test_build_client_with_real_neonize(tmp_path: Path) -> None:
    """Construct a genuine neonize client and confirm the session dir is prepared."""
    db = tmp_path / "session" / "whatsapp.db"

    client = WhatsAppWebConnector(db, _handler).build_client()

    assert client is not None
    assert db.parent.is_dir()


@pytest.mark.skipif(
    not os.environ.get("GODPY_WHATSAPP_RUN_LIVE"),
    reason="live QR pairing needs a phone; set GODPY_WHATSAPP_RUN_LIVE to run",
)
async def test_live_pairing(tmp_path: Path) -> None:  # pragma: no cover - manual only
    connector = WhatsAppWebConnector(tmp_path / "whatsapp.db", _handler)
    await connector.start()

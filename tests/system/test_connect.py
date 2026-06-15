"""System test: the telegram token verify hits the real Bot API.

Gated on a real bot token; the WhatsApp QR branch is a manual checklist (needs a phone).
"""

from __future__ import annotations

import os

import pytest

from gaia.cli.connect import _verify_telegram

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GAIA_TELEGRAM_BOT_TOKEN"),
        reason="needs a real bot token (set GAIA_TELEGRAM_BOT_TOKEN)",
    ),
]


def test_get_me_accepts_real_token() -> None:
    username = _verify_telegram(os.environ["GAIA_TELEGRAM_BOT_TOKEN"])

    assert username  # the live bot's username came back


def test_get_me_rejects_garbage_token() -> None:
    assert _verify_telegram("123456:definitely-not-a-real-token") is None

"""System test: the real health analyst triages an error digest into a valid HealthReport.

Drives the production path (``gaia.monitor.loop.analyze``) against a tmp home seeded with a mix of
a real bug + transient noise. Skipped unless a Gemini key is configured, so CI stays green.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gaia.config import Settings, get_settings
from gaia.core import Gaia
from gaia.monitor.analyst import HealthReport
from gaia.monitor.loop import analyze

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]


def test_health_analyst_returns_valid_report(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    now = datetime.now()
    lines = []
    for i in range(6):  # a recurring real bug in gaia's own code
        ts = (now - timedelta(minutes=60 - i * 5)).strftime("%Y-%m-%d %H:%M:%S,000")
        lines.append(
            json.dumps(
                {
                    "asctime": ts,
                    "message": "turn_error",
                    "error": "KeyError",
                    "detail": "'user_id'",
                    "where": "handler.py:212",
                }
            )
        )
    for i in range(2):  # transient noise the analyst should ignore
        ts = (now - timedelta(minutes=30 - i * 5)).strftime("%Y-%m-%d %H:%M:%S,000")
        lines.append(
            json.dumps(
                {
                    "asctime": ts,
                    "message": "tool_used",
                    "tool": "web_fetch",
                    "status": "error",
                    "error": "RateLimitError",
                    "detail": "429 resource_exhausted",
                }
            )
        )
    (logs / "events.jsonl").write_text("\n".join(lines) + "\n")

    config = tmp_path / "gaia.yaml"
    config.write_text(f"llm:\n  model: {get_settings().model}\nmemory:\n  enabled: false\n")
    gaia = Gaia(Settings(log_dir=logs, config_path=config, agent_registry_dir=tmp_path))
    try:
        report = asyncio.run(analyze(gaia))
    finally:
        asyncio.run(gaia.close())

    # The contract is the schema (valid HealthReport over the digest); exact verdicts are
    # model-dependent, so — like test_analyst — we don't assert specific findings.
    assert isinstance(report, HealthReport)
    assert report.summary

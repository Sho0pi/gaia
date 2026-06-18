"""System test: the real analyst turns recent usage into a valid AnalysisReport.

Drives the production path (``gaia.analysis.loop.analyze``) against a tmp home seeded
with events. Skipped unless a Gemini key is configured, so CI stays green without secrets.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gaia.analysis import AnalysisReport
from gaia.analysis.loop import analyze
from gaia.config import Settings, get_settings
from gaia.core import Gaia

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]


def test_analyst_returns_valid_report(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    now = datetime.now()
    lines = []
    for i in range(8):  # a clearly recurring search->fetch pattern
        ts = (now - timedelta(minutes=60 - i * 5)).strftime("%Y-%m-%d %H:%M:%S,000")
        lines.append(json.dumps({"asctime": ts, "message": "message_in", "user": "itay"}))
        for tool in ("web_search", "web_fetch"):
            lines.append(
                json.dumps(
                    {
                        "asctime": ts,
                        "message": "tool_used",
                        "tool": tool,
                        "status": "success",
                        "session": "s1",
                    }
                )
            )
    (logs / "events.jsonl").write_text("\n".join(lines) + "\n")

    # Honor the env-configured model (GEMINI_MODEL) like the other system tests — the
    # schema default may have no free-tier quota (gemini-2.0-flash: limit 0 → 429).
    config = tmp_path / "gaia.yaml"
    config.write_text(f"llm:\n  model: {get_settings().model}\nmemory:\n  enabled: false\n")
    gaia = Gaia(Settings(log_dir=logs, config_path=config, agent_registry_dir=tmp_path))
    try:
        report, _ = asyncio.run(analyze(gaia))
    finally:
        asyncio.run(gaia.close())

    assert isinstance(report, AnalysisReport)
    assert report.summary  # the model described the window
    # No write assertions: proposals are model-dependent; the schema is the contract.

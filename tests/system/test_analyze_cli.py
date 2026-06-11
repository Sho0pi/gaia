"""System test: the real analyst turns a crafted digest into a valid AnalysisReport.

Skipped unless a Gemini key is configured, so CI stays green without secrets.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gaia.analysis import AnalysisReport, digest_events, read_events, render_digest
from gaia.cli.analyze import _run_analyst
from gaia.config import GaiaConfig

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
)


def test_analyst_returns_valid_report(tmp_path: Path) -> None:
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
    (tmp_path / "events.jsonl").write_text("\n".join(lines) + "\n")

    digest = digest_events(read_events(tmp_path, now - timedelta(days=1)))
    report = asyncio.run(_run_analyst(GaiaConfig(), render_digest(digest)))

    assert isinstance(report, AnalysisReport)
    assert report.summary  # the model described the window
    # No write assertions: proposals are model-dependent; the schema is the contract.

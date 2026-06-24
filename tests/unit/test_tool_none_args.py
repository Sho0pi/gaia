"""Regression guard: tools must not crash when a model sends null for an optional str arg.

gpt-5.x sends an explicit null for an omitted optional function arg (Gemini omits it); ADK
passes it through as None, overriding the "" default. Every tool coerces `arg or ""` at entry —
these call each constructible tool with None args and assert it returns a dict (never raises).
A live ChatGPT turn hit this in task_create (approval_class); see [[chatgpt-null-optional-args]].
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.cron.store import CronStore
from gaia.tools.cron import make_cron
from gaia.tools.remember import make_remember
from gaia.tools.web_fetch import make_web_fetch
from gaia.tools.web_search import make_web_search


def _ctx() -> Any:
    return SimpleNamespace(user_id="itay", agent_name="gaia")


def test_cron_tolerates_null_args(tmp_path: Path) -> None:
    out = make_cron(CronStore(tmp_path / "cron.json"))(
        "list", schedule=None, message=None, name=None, job_id=None
    )
    assert isinstance(out, dict) and "status" in out


def test_web_fetch_tolerates_null_url() -> None:
    out = make_web_fetch(lambda url, max_bytes: {"text": ""})(url=None)
    assert out["status"] == "error"  # coerced "" → empty-url error, not a crash


def test_web_search_tolerates_null_query() -> None:
    out = make_web_search(lambda q, n, t: [])(query=None, time_range=None)
    assert out["status"] == "error"  # coerced "" → empty-query error


async def test_remember_tolerates_null_fact() -> None:
    out = await make_remember()(fact=None, tool_context=_ctx())
    assert isinstance(out, dict) and out["status"] == "error"

"""The cron LLM tool: action enum, chat capture, dict contracts (no scheduler needed)."""

from __future__ import annotations

from pathlib import Path

from gaia.connectors.base import current_chat
from gaia.cron import CronStore
from gaia.tools.cron import make_cron


def _tool(tmp_path: Path):
    store = CronStore(tmp_path / "cron.json")
    return make_cron(store), store


def test_add_captures_current_chat(tmp_path: Path) -> None:
    cron, store = _tool(tmp_path)
    token = current_chat.set(("telegram", "12345"))
    try:
        out = cron("add", schedule="0 9 * * *", message="AI brief", name="news")
    finally:
        current_chat.reset(token)

    assert out["status"] == "success"
    job = store.get(out["job"]["id"])
    assert job is not None
    assert (job.channel, job.chat) == ("telegram", "12345")  # delivery captured


def test_add_every_and_at_prefixes(tmp_path: Path) -> None:
    cron, _store = _tool(tmp_path)

    every = cron("add", schedule="every:60", message="tick")
    at = cron("add", schedule="at:2030-01-01T09:00:00", message="once")

    assert every["job"]["kind"] == "every" and every["job"]["expr"] == "60"
    assert at["job"]["kind"] == "at" and at["job"]["delete_after_run"] is True


def test_add_invalid_schedule_errors(tmp_path: Path) -> None:
    cron, _ = _tool(tmp_path)

    out = cron("add", schedule="every:5", message="hot")

    assert out["status"] == "error"
    assert "minimum 30" in out["error_message"]


def test_list_get_remove_enable_disable(tmp_path: Path) -> None:
    cron, _ = _tool(tmp_path)
    job_id = cron("add", schedule="0 9 * * *", message="x")["job"]["id"]

    assert [j["id"] for j in cron("list")["jobs"]] == [job_id]
    assert cron("get", job_id=job_id)["job"]["id"] == job_id
    assert cron("disable", job_id=job_id)["job"]["enabled"] is False
    assert cron("enable", job_id=job_id)["job"]["enabled"] is True
    assert cron("remove", job_id=job_id)["removed"] == job_id
    assert cron("get", job_id=job_id)["status"] == "error"


def test_update_replaces_only_given_fields(tmp_path: Path) -> None:
    cron, _store = _tool(tmp_path)
    job_id = cron("add", schedule="0 9 * * *", message="old", name="keep")["job"]["id"]

    out = cron("update", job_id=job_id, message="new message")

    assert out["job"]["message"] == "new message"
    assert out["job"]["name"] == "keep"  # untouched
    assert out["job"]["expr"] == "0 9 * * *"


def test_unknown_action_errors(tmp_path: Path) -> None:
    cron, _ = _tool(tmp_path)

    out = cron("explode")

    assert out["status"] == "error"
    assert "unknown action" in out["error_message"]

"""ask tool: pending-ticket shape, input validation, logging, long-running registration."""

from __future__ import annotations

import pytest

from godpy.config import GodConfig, ToolConfig
from godpy.tools import ask as ask_mod
from godpy.tools import default_registry
from godpy.tools.ask import make_ask


def test_pending_ticket_shape() -> None:
    out = make_ask()("Which environment?", options=["dev", "prod"])

    assert out["status"] == "pending"
    assert out["question"] == "Which environment?"
    assert out["options"] == ["dev", "prod"]
    assert out["ask_id"]  # non-empty correlation token


def test_free_text_question_has_no_options() -> None:
    out = make_ask()("  What's your name?  ")

    assert out["status"] == "pending"
    assert out["question"] == "What's your name?"  # stripped
    assert out["options"] == []


def test_empty_question_returns_error_dict() -> None:
    out = make_ask()("   ")

    assert out["status"] == "error"
    assert "empty" in out["error_message"]


def test_mismatched_option_descriptions_returns_error_dict() -> None:
    out = make_ask()("Pick", options=["a", "b"], option_descriptions=["only one"])

    assert out["status"] == "error"
    assert "option_descriptions" in out["error_message"]


def test_multi_select_passthrough_and_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(ask_mod, "log_event", lambda action, **f: events.append((action, f)))

    out = make_ask()("Pick some", options=["a", "b", "c"], multi_select=True)

    assert out["status"] == "pending"
    assert events == [
        ("tool_used", {"tool": "ask", "status": "pending", "n_options": 3, "multi_select": True}),
    ]


def test_error_path_logs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(ask_mod, "log_event", lambda action, **f: events.append((action, f)))

    make_ask()("")

    assert len(events) == 1
    assert events[0][1]["status"] == "error"


def test_registered_as_long_running_by_default() -> None:
    tool = default_registry().get("ask")

    assert getattr(tool, "is_long_running", False) is True


def test_ask_removed_when_disabled() -> None:
    config = GodConfig(tools={"ask": ToolConfig(enabled=False)})

    assert "ask" not in default_registry(config).names()

"""The ChatGPT (Codex) backend drops a model's null for an omitted optional tool arg.

gpt-5.x sends an explicit ``null`` for an arg it left unset (Gemini just omits it). If that
``None`` reached a tool it would override the tool's ``""`` default and crash its string ops, so
``responses_llm`` strips nulls when parsing the function call — the tool then sees only the args
the model actually set and uses its defaults for the rest. Fixed at the source (one backend),
so no tool needs its own null handling. See [[chatgpt-null-optional-args]].
"""

from __future__ import annotations

from gaia.providers.openai.responses_llm import _call_args


def test_drops_null_args() -> None:
    assert _call_args('{"title": "buy milk", "approval_class": null, "spec": null}') == {
        "title": "buy milk"
    }


def test_keeps_set_args_and_falsy_non_null() -> None:
    # Empty string / 0 / false are real values the model chose — only null is dropped.
    assert _call_args('{"a": "", "b": 0, "c": false, "d": null}') == {"a": "", "b": 0, "c": False}


def test_empty_or_missing_arguments() -> None:
    assert _call_args(None) == {}
    assert _call_args("{}") == {}

"""Connector-agnostic ask rendering: numbered-list floor + numeric-reply resolution."""

from __future__ import annotations

from godpy.connectors.base import Ask, render_ask_text, resolve_option_reply


def _ask(options: list[str], *, multi: bool = False, descs: list[str] | None = None) -> Ask:
    return Ask(
        question="Pick an environment",
        options=options,
        option_descriptions=descs,
        multi_select=multi,
        ask_id="x",
    )


def test_render_free_text_is_just_the_question() -> None:
    ask = Ask("What's your name?", [], None, False, "x")

    assert render_ask_text(ask) == "What's your name?"


def test_render_numbers_options_with_hint() -> None:
    text = render_ask_text(_ask(["dev", "prod"]))

    assert "1. dev" in text
    assert "2. prod" in text
    assert "number of your choice" in text


def test_render_includes_descriptions_and_multi_hint() -> None:
    text = render_ask_text(_ask(["dev", "prod"], multi=True, descs=["staging", "live"]))

    assert "1. dev — staging" in text
    assert "comma-separated" in text


def test_resolve_single_number_to_label() -> None:
    assert resolve_option_reply(_ask(["dev", "prod"]), "2") == "prod"


def test_resolve_multi_numbers_to_labels() -> None:
    ask = _ask(["a", "b", "c"], multi=True)

    assert resolve_option_reply(ask, "1, 3") == "a, c"


def test_resolve_passes_through_free_text() -> None:
    ask = _ask(["dev", "prod"])

    assert resolve_option_reply(ask, "actually staging") == "actually staging"


def test_resolve_out_of_range_is_free_text() -> None:
    assert resolve_option_reply(_ask(["dev", "prod"]), "5") == "5"


def test_resolve_rejects_multiple_numbers_when_single_select() -> None:
    # "1 2" is not a valid single-select answer, so it's treated as free text.
    assert resolve_option_reply(_ask(["dev", "prod"]), "1 2") == "1 2"


def test_resolve_without_options_returns_text() -> None:
    ask = Ask("free?", [], None, False, "x")

    assert resolve_option_reply(ask, "anything") == "anything"

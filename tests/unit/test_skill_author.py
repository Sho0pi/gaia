"""skill_author output parsing (the model researches with tools, then emits this format)."""

from __future__ import annotations

from gaia.agents.skill_author import _parse_draft


def test_parse_first_line_description_rest_body() -> None:
    desc, body = _parse_draft(
        "Write tight tweets.\n\nKeep it under 280 chars.", fallback_description="x"
    )
    assert desc == "Write tight tweets."
    assert body == "Keep it under 280 chars."


def test_parse_strips_code_fences() -> None:
    text = "```markdown\nSummarize PDFs.\n\nExtract the key points.\n```"
    desc, body = _parse_draft(text, fallback_description="x")
    assert desc == "Summarize PDFs." and body == "Extract the key points."


def test_parse_strips_leading_heading_hashes() -> None:
    desc, _ = _parse_draft("# A great skill\n\nbody", fallback_description="x")
    assert desc == "A great skill"


def test_parse_empty_uses_fallback() -> None:
    desc, _ = _parse_draft("   \n  ", fallback_description="fallback")
    assert desc == "fallback"

"""`chunk_text` — split long replies under a channel's size cap (#56)."""

from __future__ import annotations

from gaia.connectors.base import TELEGRAM_LIMIT, chunk_text


def test_under_limit_is_one_unchanged_chunk() -> None:
    assert chunk_text("hello world", 4096) == ["hello world"]


def test_splits_on_paragraph_boundary_under_limit() -> None:
    para = "x" * 3000
    out = chunk_text(f"{para}\n\n{para}", limit=4096)

    assert len(out) == 2  # one paragraph per chunk (joining both would exceed 4096)
    assert all(len(c) <= 4096 for c in out)
    assert out[0] == para and out[1] == para  # content preserved


def test_oversize_single_word_is_hard_sliced() -> None:
    out = chunk_text("a" * 10_000, limit=4096)

    assert [len(c) for c in out] == [4096, 4096, 1808]  # 10000 = 4096+4096+1808
    assert "".join(out) == "a" * 10_000  # nothing lost


def test_nine_thousand_chars_splits_into_three_telegram_messages() -> None:
    out = chunk_text("word " * 1800, TELEGRAM_LIMIT)  # 9000 chars, all on one line

    assert len(out) == 3 and all(len(c) <= TELEGRAM_LIMIT for c in out)


def test_packs_greedily_up_to_the_limit() -> None:
    # Three 2000-char lines, limit 4096: first two pack together, third alone.
    line = "y" * 2000
    out = chunk_text("\n".join([line, line, line]), limit=4096)

    assert len(out) == 2 and out[0] == f"{line}\n{line}" and out[1] == line

from __future__ import annotations

import pytest

from aether.app.ingestion.chunking import _HARD_MAX_TOKENS, _TARGET_TOKENS, chunk_document
from aether.app.ingestion.document_tree import DocumentNode, NodeKind

pytestmark = pytest.mark.unit


def _heading(text: str, level: int) -> DocumentNode:
    return DocumentNode(kind=NodeKind.HEADING, text=text, level=level, page=None)


def _para(text: str, page: int | None = None) -> DocumentNode:
    return DocumentNode(kind=NodeKind.PARAGRAPH, text=text, level=None, page=page)


def _table(text: str) -> DocumentNode:
    return DocumentNode(kind=NodeKind.TABLE, text=text, level=None, page=None)


def _long_sentence(word_count: int, *, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{i}" for i in range(word_count)) + "."


def test_small_document_becomes_one_chunk_with_correct_provenance() -> None:
    nodes = [
        _heading("Title", 1),
        _para("First sentence. Second sentence.", page=1),
    ]

    chunks = chunk_document(nodes)

    assert len(chunks) == 1
    assert chunks[0].section_path == "Title"
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1
    assert "First sentence." in chunks[0].content
    assert "Second sentence." in chunks[0].content
    assert chunks[0].char_start == 0
    assert chunks[0].char_end > 0


def test_chunks_never_exceed_the_hard_max_token_count() -> None:
    # One giant paragraph, well past both the target and hard max.
    sentences = [_long_sentence(60) for _ in range(20)]  # ~20 long sentences
    nodes = [_heading("Title", 1), _para(" ".join(sentences))]

    chunks = chunk_document(nodes)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= _HARD_MAX_TOKENS


def test_chunking_never_splits_a_sentence_across_two_chunks() -> None:
    sentences = [f"This is sentence number {i} in the document." for i in range(80)]
    nodes = [_heading("Title", 1), _para(" ".join(sentences))]

    chunks = chunk_document(nodes)

    # Every sentence appears whole in exactly the chunk(s) it belongs to
    # (it may appear in two adjacent chunks due to overlap, but never as
    # a truncated fragment).
    for sentence in sentences:
        assert any(sentence in c.content for c in chunks), f"missing whole: {sentence!r}"


def test_a_new_top_level_heading_always_starts_a_fresh_chunk() -> None:
    nodes = [
        _heading("Section One", 1),
        _para("Short content in section one."),
        _heading("Section Two", 1),
        _para("Short content in section two."),
    ]

    chunks = chunk_document(nodes)

    assert len(chunks) == 2
    assert chunks[0].section_path == "Section One"
    assert chunks[1].section_path == "Section Two"
    assert "section one" in chunks[0].content.lower()
    assert "section two" in chunks[1].content.lower()
    assert "section two" not in chunks[0].content.lower()


def test_sub_headings_do_not_force_a_new_chunk_only_top_level_does() -> None:
    nodes = [
        _heading("Title", 1),
        _para("Intro."),
        _heading("Subsection", 2),
        _para("More content under the subsection."),
    ]

    chunks = chunk_document(nodes)

    # Short enough to fit in one chunk together — a level-2 heading is
    # not a top-level boundary.
    assert len(chunks) == 1
    assert chunks[0].section_path == "Title > Subsection"


def test_consecutive_chunks_within_a_section_overlap() -> None:
    sentences = [f"This is sentence number {i} in a long document body." for i in range(80)]
    nodes = [_heading("Title", 1), _para(" ".join(sentences))]

    chunks = chunk_document(nodes)

    assert len(chunks) >= 2
    # The tail of chunk N should reappear at the head of chunk N+1.
    first_chunk_sentences = [s for s in sentences if s in chunks[0].content]
    second_chunk_sentences = [s for s in sentences if s in chunks[1].content]
    overlap = set(first_chunk_sentences) & set(second_chunk_sentences)
    assert len(overlap) > 0, "expected some sentence overlap between consecutive chunks"


def test_tables_are_never_split_and_always_form_their_own_chunk() -> None:
    table_text = "H1 | H2\n" + "\n".join(f"r{i}c1 | r{i}c2" for i in range(50))
    nodes = [
        _heading("Title", 1),
        _para("Some intro text."),
        _table(table_text),
        _para("Some text after the table."),
    ]

    chunks = chunk_document(nodes)

    table_chunks = [c for c in chunks if c.content == table_text]
    assert len(table_chunks) == 1
    assert table_chunks[0].content == table_text  # whole, byte-for-byte, never fragmented


def test_chunks_target_roughly_512_tokens_when_content_allows() -> None:
    sentences = [
        f"This is a moderately long filler sentence number {i} for testing." for i in range(200)
    ]
    nodes = [_heading("Title", 1), _para(" ".join(sentences))]

    chunks = chunk_document(nodes)

    # Not every chunk hits the target exactly (the last one in a run is
    # often smaller), but at least one non-final chunk should land in
    # the target's neighborhood, proving the packer isn't chunking far
    # too eagerly or too lazily.
    non_final = chunks[:-1]
    assert any(c.token_count >= _TARGET_TOKENS * 0.8 for c in non_final)


def test_empty_document_produces_no_chunks() -> None:
    assert chunk_document([]) == []

"""Structure-aware chunking (ADR-6.2): pack a normalized document tree
into ~512-token chunks (hard max 800, 10-15% overlap), never splitting
mid-sentence, never crossing a top-level (level-1) heading boundary.
Tables are chunked whole. Every chunk carries provenance captured at
chunk-creation time — section_path, page range, char span (offsets into
the document's flattened text), token count.

The unit of packing below the paragraph level is the *sentence*, not
the token: token-level splitting is what "never split mid-sentence"
explicitly rules out, so sentences are the smallest movable piece.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise

import tiktoken

from aether.app.ingestion.document_tree import DocumentNode, NodeKind
from aether.domain.entities import ChunkDraft

_TARGET_TOKENS = 512
_HARD_MAX_TOKENS = 800
_OVERLAP_RATIO = 0.125  # midpoint of ADR-6.2's stated 10-15% range
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_NODE_SEPARATOR = "\n\n"

_encoding = tiktoken.get_encoding("cl100k_base")


@dataclass(frozen=True, slots=True)
class _Sentence:
    text: str
    section_path: str
    page: int | None
    char_start: int
    char_end: int
    starts_new_top_level_section: bool
    token_count: int


def chunk_document(nodes: list[DocumentNode]) -> list[ChunkDraft]:
    sentences, table_positions = _flatten_to_sentences(nodes)

    chunks: list[ChunkDraft] = []
    buffer: list[_Sentence] = []
    for item in _interleave(sentences, table_positions):
        if isinstance(item, _TableItem):
            _flush(buffer, chunks)
            buffer = []
            chunks.append(
                ChunkDraft(
                    content=item.text,
                    section_path=item.section_path,
                    page_start=item.page,
                    page_end=item.page,
                    char_start=item.char_start,
                    char_end=item.char_end,
                    token_count=_count_tokens(item.text),
                )
            )
            continue

        sentence = item
        if sentence.starts_new_top_level_section and buffer:
            # Never cross a top-level section boundary — close out
            # whatever's accumulated so far, no overlap carried across
            # this specific boundary (overlap is a within-section
            # continuity aid, not a cross-topic one).
            _flush(buffer, chunks)
            buffer = []

        current_tokens = sum(s.token_count for s in buffer)
        if buffer and current_tokens + sentence.token_count > _HARD_MAX_TOKENS:
            _flush(buffer, chunks)
            buffer = _overlap_tail(buffer)

        buffer.append(sentence)
        current_tokens = sum(s.token_count for s in buffer)
        if current_tokens >= _TARGET_TOKENS:
            _flush(buffer, chunks)
            buffer = _overlap_tail(buffer)

    _flush(buffer, chunks)
    return chunks


def _flush(buffer: list[_Sentence], chunks: list[ChunkDraft]) -> None:
    if not buffer:
        return
    text = " ".join(s.text for s in buffer)
    pages = [s.page for s in buffer if s.page is not None]
    chunks.append(
        ChunkDraft(
            content=text,
            # A chunk can legitimately span into a deeper sub-heading
            # (only *top-level* headings force a fresh chunk) — the
            # section_path reported is wherever the chunk actually ends,
            # the most specific context by the time it closes, not
            # wherever its first sentence happened to start.
            section_path=buffer[-1].section_path,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            char_start=buffer[0].char_start,
            char_end=buffer[-1].char_end,
            token_count=sum(s.token_count for s in buffer),
        )
    )


def _overlap_tail(buffer: list[_Sentence]) -> list[_Sentence]:
    """The trailing ~10-15% of the just-flushed chunk's tokens, carried
    into the next chunk's start — ADR-6.2's overlap requirement, applied
    whole-sentence so it never re-splits mid-sentence either."""
    total = sum(s.token_count for s in buffer)
    target_overlap = total * _OVERLAP_RATIO
    tail: list[_Sentence] = []
    accumulated = 0
    for sentence in reversed(buffer):
        if accumulated >= target_overlap:
            break
        tail.insert(0, sentence)
        accumulated += sentence.token_count
    return tail


def _count_tokens(text: str) -> int:
    return len(_encoding.encode(text))


@dataclass(frozen=True, slots=True)
class _TableItem:
    text: str
    section_path: str
    page: int | None
    char_start: int
    char_end: int


def _flatten_to_sentences(
    nodes: list[DocumentNode],
) -> tuple[list[_Sentence], dict[int, _TableItem]]:
    sentences: list[_Sentence] = []
    table_positions: dict[int, _TableItem] = {}
    heading_stack: list[tuple[int, str]] = []
    offset = 0

    for node in nodes:
        if node.kind == NodeKind.HEADING:
            level = node.level or 1
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, node.text))
            section_path = " > ".join(title for _, title in heading_stack)
            start = offset
            end = start + len(node.text)
            sentences.append(
                _Sentence(
                    text=node.text,
                    section_path=section_path,
                    page=node.page,
                    char_start=start,
                    char_end=end,
                    starts_new_top_level_section=(level == 1),
                    token_count=_count_tokens(node.text),
                )
            )
            offset = end + len(_NODE_SEPARATOR)
            continue

        section_path = " > ".join(title for _, title in heading_stack)

        if node.kind == NodeKind.TABLE:
            start = offset
            end = start + len(node.text)
            table_positions[len(sentences)] = _TableItem(
                text=node.text,
                section_path=section_path,
                page=node.page,
                char_start=start,
                char_end=end,
            )
            offset = end + len(_NODE_SEPARATOR)
            continue

        # PARAGRAPH: split into sentences, each a separately movable atom.
        node_start = offset
        for local_start, local_end, raw_sentence in _split_sentences(node.text):
            sentences.append(
                _Sentence(
                    text=raw_sentence,
                    section_path=section_path,
                    page=node.page,
                    char_start=node_start + local_start,
                    char_end=node_start + local_end,
                    starts_new_top_level_section=False,
                    token_count=_count_tokens(raw_sentence),
                )
            )
        offset = node_start + len(node.text) + len(_NODE_SEPARATOR)

    return sentences, table_positions


def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Splits on sentence-ending punctuation, returning each sentence's
    own (start, end) offsets *within this paragraph's original text* —
    computed from the boundary match positions directly, not by
    re-searching for the (post-strip) sentence text, which could
    silently misattribute offsets for a repeated phrase."""
    boundaries = [0, *(m.end() for m in _SENTENCE_BOUNDARY.finditer(text)), len(text)]
    results: list[tuple[int, int, str]] = []
    for start, end in pairwise(boundaries):
        raw = text[start:end]
        stripped = raw.strip()
        if not stripped:
            continue
        leading_ws = len(raw) - len(raw.lstrip())
        trailing_ws = len(raw) - len(raw.rstrip())
        results.append((start + leading_ws, end - trailing_ws, stripped))
    return results


def _interleave(
    sentences: list[_Sentence], table_positions: dict[int, _TableItem]
) -> Iterator[_Sentence | _TableItem]:
    for index, sentence in enumerate(sentences):
        if index in table_positions:
            yield table_positions[index]
        yield sentence
    if len(sentences) in table_positions:
        yield table_positions[len(sentences)]

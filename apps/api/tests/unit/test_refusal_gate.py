from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.retrieval.hybrid_search import RankedChunk, RetrievalResult
from aether.app.retrieval.refusal_gate import RetrievalGate

pytestmark = pytest.mark.unit


def _chunk(score: float) -> RankedChunk:
    return RankedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_title="doc.md",
        section_path="Intro",
        page_start=None,
        page_end=None,
        content="some content",
        fused_score=score,
    )


def test_empty_retrieval_never_passes() -> None:
    gate = RetrievalGate(threshold=0.01)

    decision = gate.evaluate(RetrievalResult(chunks=[], degraded=False))

    assert decision.passed is False
    assert decision.top_score is None


def test_a_score_below_threshold_fails_the_gate() -> None:
    gate = RetrievalGate(threshold=0.02)

    decision = gate.evaluate(RetrievalResult(chunks=[_chunk(0.01)], degraded=False))

    assert decision.passed is False
    assert decision.top_score == 0.01


def test_a_score_at_or_above_threshold_passes_the_gate() -> None:
    gate = RetrievalGate(threshold=0.02)

    at_threshold = gate.evaluate(RetrievalResult(chunks=[_chunk(0.02)], degraded=False))
    above_threshold = gate.evaluate(RetrievalResult(chunks=[_chunk(0.05)], degraded=False))

    assert at_threshold.passed is True
    assert above_threshold.passed is True


def test_only_the_top_ranked_chunks_score_is_considered() -> None:
    gate = RetrievalGate(threshold=0.02)

    decision = gate.evaluate(RetrievalResult(chunks=[_chunk(0.05), _chunk(0.001)], degraded=False))

    assert decision.passed is True
    assert decision.top_score == 0.05

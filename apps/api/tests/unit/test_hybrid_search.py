from __future__ import annotations

from uuid import uuid4

import pytest

from aether.app.retrieval.hybrid_search import HybridSearch
from aether.ports.retrieval import ChunkSearchResult
from tests.unit.fakes.retrieval import FakeChunkSearch, FakeQueryEmbedder

pytestmark = pytest.mark.unit


def _result(
    *, chunk_id=None, document_id=None, title="doc.md", section="Intro", score=0.5, embedding=None
) -> ChunkSearchResult:
    return ChunkSearchResult(
        chunk_id=chunk_id or uuid4(),
        document_id=document_id or uuid4(),
        document_title=title,
        section_path=section,
        page_start=None,
        page_end=None,
        content=f"content for {section}",
        embedding=embedding,
        score=score,
    )


async def test_a_chunk_ranked_in_both_legs_outranks_one_ranked_in_only_one() -> None:
    both_legs = _result(section="both", embedding=[1.0, 0.0, 0.0, 0.0])
    vector_only = _result(section="vector-only", embedding=[0.0, 1.0, 0.0, 0.0])

    chunk_search = FakeChunkSearch(
        vector_results=[both_legs, vector_only],
        lexical_results=[both_legs],
    )
    search = HybridSearch(chunk_search=chunk_search, embedder=FakeQueryEmbedder())

    result = await search.search(uuid4(), query="q", k=2)

    assert result.chunks[0].section_path == "both"


async def test_a_lexical_only_hit_still_surfaces_hybrids_whole_point() -> None:
    """A chunk the vector leg never returns (e.g. an exact acronym/ID
    embeddings fumble) must still reach the final results via the
    lexical leg — this is the literal reason hybrid exists over
    vector-only retrieval."""
    vector_hits = [_result(section=f"vector-{i}", embedding=[float(i), 0, 0, 0]) for i in range(3)]
    lexical_only_hit = _result(section="ACME-PART-99231", embedding=[0.0, 0.0, 1.0, 0.0])

    chunk_search = FakeChunkSearch(
        vector_results=vector_hits,
        lexical_results=[lexical_only_hit],
    )
    search = HybridSearch(chunk_search=chunk_search, embedder=FakeQueryEmbedder())

    result = await search.search(uuid4(), query="ACME-PART-99231", k=4)

    assert any(c.section_path == "ACME-PART-99231" for c in result.chunks)


async def test_mmr_prefers_a_diverse_chunk_over_a_near_duplicate_of_an_already_selected_one() -> (
    None
):
    top = _result(section="top", score=1.0, embedding=[1.0, 0.0, 0.0, 0.0])
    near_duplicate_of_top = _result(
        section="near-dup-of-top", score=0.9, embedding=[0.99, 0.01, 0.0, 0.0]
    )
    genuinely_different = _result(section="different", score=0.5, embedding=[0.0, 0.0, 0.0, 1.0])
    chunk_search = FakeChunkSearch(
        vector_results=[top, near_duplicate_of_top, genuinely_different], lexical_results=[]
    )
    search = HybridSearch(chunk_search=chunk_search, embedder=FakeQueryEmbedder())

    result = await search.search(uuid4(), query="q", k=2)

    sections = [c.section_path for c in result.chunks]
    assert sections[0] == "top"
    # Raw RRF order would pick the near-duplicate second (higher raw
    # score) — MMR's diversity penalty must prefer the genuinely
    # different chunk instead.
    assert "different" in sections
    assert "near-dup-of-top" not in sections


async def test_vector_leg_failure_sets_degraded_and_falls_back_to_lexical_only() -> None:
    lexical_hit = _result(section="lexical", embedding=[1.0, 0.0, 0.0, 0.0])
    chunk_search = FakeChunkSearch(
        lexical_results=[lexical_hit], vector_error=ConnectionError("index unreachable")
    )
    search = HybridSearch(chunk_search=chunk_search, embedder=FakeQueryEmbedder())

    result = await search.search(uuid4(), query="q", k=4)

    assert result.degraded is True
    assert [c.section_path for c in result.chunks] == ["lexical"]


async def test_k_bounds_the_number_of_returned_chunks() -> None:
    results = [_result(section=f"s{i}", embedding=[float(i), 0, 0, 0]) for i in range(10)]
    chunk_search = FakeChunkSearch(vector_results=results, lexical_results=[])
    search = HybridSearch(chunk_search=chunk_search, embedder=FakeQueryEmbedder())

    result = await search.search(uuid4(), query="q", k=3)

    assert len(result.chunks) == 3


async def test_empty_results_from_both_legs_is_a_clean_empty_result_not_an_error() -> None:
    chunk_search = FakeChunkSearch(vector_results=[], lexical_results=[])
    search = HybridSearch(chunk_search=chunk_search, embedder=FakeQueryEmbedder())

    result = await search.search(uuid4(), query="nothing matches this", k=6)

    assert result.chunks == []
    assert result.degraded is False


async def test_workspace_id_and_query_are_passed_through_to_both_legs() -> None:
    workspace_id = uuid4()
    chunk_search = FakeChunkSearch()
    search = HybridSearch(chunk_search=chunk_search, embedder=FakeQueryEmbedder())

    await search.search(workspace_id, query="hello world", k=6)

    assert chunk_search.vector_calls[0][0] == workspace_id
    assert chunk_search.lexical_calls[0][0] == workspace_id
    assert chunk_search.lexical_calls[0][1] == "hello world"

from __future__ import annotations

import pytest

from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter

pytestmark = pytest.mark.unit


async def test_identical_text_always_produces_the_identical_vector() -> None:
    adapter = LocalHashEmbeddingAdapter()
    [first] = await adapter.embed_batch(["the same content"])
    [second] = await adapter.embed_batch(["the same content"])
    assert first == second


async def test_different_text_produces_different_vectors() -> None:
    adapter = LocalHashEmbeddingAdapter()
    vectors = await adapter.embed_batch(["content a", "content b"])
    assert vectors[0] != vectors[1]


async def test_vectors_match_the_real_pgvector_column_dimensionality() -> None:
    adapter = LocalHashEmbeddingAdapter()
    [vector] = await adapter.embed_batch(["anything"])
    assert len(vector) == 1536
    assert all(-1.0 <= v <= 1.0 for v in vector)


async def test_batch_order_is_preserved() -> None:
    adapter = LocalHashEmbeddingAdapter()
    texts = [f"text-{i}" for i in range(10)]
    vectors = await adapter.embed_batch(texts)
    single_vectors = [(await adapter.embed_batch([t]))[0] for t in texts]
    assert vectors == single_vectors

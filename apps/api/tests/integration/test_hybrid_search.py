"""Real-Postgres proof for hybrid retrieval (issue #56, §3.2.5, ADR-6.3)
— runs as app_api, the same role the running HTTP process uses.

The vector-vs-lexical comparison test deliberately hand-crafts chunk
embeddings (a standard IR-testing technique) rather than relying on a
real embedding model's semantic quality: no real OpenAI/Anthropic key
exists in this dev environment (LocalHashEmbeddingAdapter is a real,
honest, non-semantic fallback — see its own docstring), so proving
"hybrid beats vector-only because embeddings miss exact terms" against
a genuinely semantic model isn't possible here. Setting a target
chunk's embedding to the query embedding's antipode and decoy chunks'
embeddings close to the query proves the same structural claim
deterministically and honestly: a real Postgres HNSW search, real RLS,
real RRF fusion — only the embedding *values* are engineered, not the
retrieval logic being tested.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.postgres.chunk_search import PostgresChunkSearch
from aether.app.retrieval.hybrid_search import HybridSearch

pytestmark = pytest.mark.integration


async def _seed_workspace(bootstrap_pool: asyncpg.Pool) -> uuid.UUID:
    workspace_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Retrieval Test', $2)",
            workspace_id,
            f"retrieval-test-{workspace_id}",
        )
    return workspace_id


async def _seed_ready_document(bootstrap_pool: asyncpg.Pool, workspace_id: uuid.UUID) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key, status) VALUES "
            "($1, $2, 'catalog.md', 'x', 'text/markdown', 100, 'k', 'ready')",
            document_id,
            workspace_id,
        )
    return document_id


async def _seed_chunk(
    bootstrap_pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    content: str,
    embedding: list[float],
    section_path: str = "Body",
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count, embedding, embedding_model, "
            "embedding_version) VALUES ($1, $2, $3, $4, 0, $5, $6, $7, 3, $8, "
            "'local-hash-fallback', 1)",
            chunk_id,
            workspace_id,
            document_id,
            section_path,
            len(content),
            content,
            content,  # content_sha256 column just needs a value; not exercised here
            embedding,
        )
    return chunk_id


def _antipode(vector: list[float]) -> list[float]:
    return [-v for v in vector]


def _near(vector: list[float], *, nudge: float = 0.001) -> list[float]:
    return [v + nudge for v in vector]


async def test_hybrid_surfaces_an_exact_term_match_vector_only_search_would_miss(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id = await _seed_workspace(db_bootstrap_pool)
    document_id = await _seed_ready_document(db_bootstrap_pool, workspace_id)
    embedder = LocalHashEmbeddingAdapter()
    (query_embedding,) = await embedder.embed_batch(["XJ99231-PART replacement procedure"])

    target_content = "The replacement component is XJ99231-PART, available in stock now."
    await _seed_chunk(
        db_bootstrap_pool,
        workspace_id=workspace_id,
        document_id=document_id,
        content=target_content,
        embedding=_antipode(query_embedding),  # guaranteed last place in vector-leg ranking
        section_path="target",
    )
    for i in range(5):
        await _seed_chunk(
            db_bootstrap_pool,
            workspace_id=workspace_id,
            document_id=document_id,
            content=f"Unrelated generic filler paragraph number {i} about nothing in particular.",
            embedding=_near(query_embedding, nudge=0.0001 * (i + 1)),
            section_path=f"decoy-{i}",
        )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        chunk_search = PostgresChunkSearch(conn)

        vector_only = await chunk_search.search_vector(
            workspace_id, embedding=query_embedding, limit=3
        )
        assert "target" not in [r.section_path for r in vector_only]

        lexical_only = await chunk_search.search_lexical(
            workspace_id, query="XJ99231-PART", limit=10
        )
        assert "target" in [r.section_path for r in lexical_only]

        hybrid = HybridSearch(chunk_search=chunk_search, embedder=embedder)
        fused = await hybrid.search(workspace_id, query="XJ99231-PART", k=6)
        assert "target" in [c.section_path for c in fused.chunks]


async def test_retrieval_cannot_see_another_workspaces_chunks(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_a = await _seed_workspace(db_bootstrap_pool)
    workspace_b = await _seed_workspace(db_bootstrap_pool)
    document_b = await _seed_ready_document(db_bootstrap_pool, workspace_b)
    embedder = LocalHashEmbeddingAdapter()
    (embedding,) = await embedder.embed_batch(["secret content"])
    await _seed_chunk(
        db_bootstrap_pool,
        workspace_id=workspace_b,
        document_id=document_b,
        content="workspace B's confidential secret content",
        embedding=embedding,
        section_path="secret",
    )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_a))
        chunk_search = PostgresChunkSearch(conn)
        hybrid = HybridSearch(chunk_search=chunk_search, embedder=embedder)

        result = await hybrid.search(workspace_a, query="secret content", k=6)

    assert result.chunks == []


async def test_a_document_still_mid_pipeline_is_invisible_to_retrieval(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id = await _seed_workspace(db_bootstrap_pool)
    document_id = uuid.uuid4()
    embedder = LocalHashEmbeddingAdapter()
    (embedding,) = await embedder.embed_batch(["mid pipeline content"])
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key, status) VALUES "
            "($1, $2, 'wip.md', 'x', 'text/markdown', 100, 'k', 'embedding')",
            document_id,
            workspace_id,
        )
    await _seed_chunk(
        db_bootstrap_pool,
        workspace_id=workspace_id,
        document_id=document_id,
        content="mid pipeline content not yet ready",
        embedding=embedding,
        section_path="not-ready-yet",
    )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        chunk_search = PostgresChunkSearch(conn)
        hybrid = HybridSearch(chunk_search=chunk_search, embedder=embedder)

        result = await hybrid.search(workspace_id, query="mid pipeline content", k=6)

    assert result.chunks == []

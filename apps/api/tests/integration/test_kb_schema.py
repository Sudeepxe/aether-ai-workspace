"""RLS canary + schema invariants for documents/chunks (Sprint 5, issue
#43) — same falsifying-test discipline as test_chat_schema.py: every
assertion runs against app_api, never a superuser.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.security]


async def _seed_two_tenants(bootstrap_pool: asyncpg.Pool) -> dict[str, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Tenant A', $2), ($3, 'Tenant B', $4)",
            tenant_a,
            f"tenant-a-{tenant_a}",
            tenant_b,
            f"tenant-b-{tenant_b}",
        )
    return {"tenant_a": tenant_a, "tenant_b": tenant_b}


async def test_tenant_cannot_read_another_tenants_documents(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    doc_id = uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_b"]))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
            doc_id,
            ids["tenant_b"],
        )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        row = await conn.fetchrow("SELECT id FROM documents WHERE id = $1", doc_id)
        assert row is None, "tenant A must not see tenant B's document"

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_b"]))
        row = await conn.fetchrow("SELECT id FROM documents WHERE id = $1", doc_id)
        assert row is not None, "tenant B must see its own document"


async def test_tenant_cannot_write_a_document_under_another_tenants_id(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
                "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
                uuid.uuid4(),
                ids["tenant_b"],  # mismatched tenant — WITH CHECK must refuse
            )


async def test_tenant_cannot_read_another_tenants_chunks(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    doc_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_b"]))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
            doc_id,
            ids["tenant_b"],
        )
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count) "
            "VALUES ($1, $2, $3, 'Intro', 0, 10, 'hello there', 'h', 2)",
            chunk_id,
            ids["tenant_b"],
            doc_id,
        )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        row = await conn.fetchrow("SELECT id FROM chunks WHERE id = $1", chunk_id)
        assert row is None, "tenant A must not see tenant B's chunk"


async def test_deleting_a_document_cascades_to_its_chunks(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    """The ON DELETE CASCADE FK — proves a document delete really does
    remove its chunks at the database level, the mechanism FR-KB-5's
    provable deletion relies on."""
    ids = await _seed_two_tenants(db_bootstrap_pool)
    doc_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
            doc_id,
            ids["tenant_a"],
        )
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count) "
            "VALUES ($1, $2, $3, 'Intro', 0, 10, 'hello there', 'h', 2)",
            chunk_id,
            ids["tenant_a"],
            doc_id,
        )
        await conn.execute("DELETE FROM documents WHERE id = $1", doc_id)
        remaining = await conn.fetchrow("SELECT id FROM chunks WHERE id = $1", chunk_id)
    assert remaining is None


async def test_chunk_embedding_round_trips_as_a_plain_float_list(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    """Proves the pgvector asyncpg codec (registered in
    adapters/postgres/pool.py's _init_connection) actually works end to
    end. It returns a pgvector.Vector wrapper, not a bare list — future
    chunk-repository adapters must call .to_list() explicitly before an
    embedding reaches the domain layer (ADR-3.4: no pgvector-specific
    type crosses into domain/entities.py); this test documents that
    conversion point rather than assuming asyncpg does it for free."""
    ids = await _seed_two_tenants(db_bootstrap_pool)
    doc_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    vector = [0.1] * 1536

    # Seeded via the bootstrap pool: app_api has no INSERT grant on
    # chunks (chunk content is worker-only, see the migration's grant
    # comments) — this test is specifically about the codec on *read*.
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
            doc_id,
            ids["tenant_a"],
        )
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count, embedding, embedding_model, "
            "embedding_version) VALUES ($1, $2, $3, 'Intro', 0, 10, 'hello there', 'h', 2, "
            "$4, 'text-embedding-3-small', 1)",
            chunk_id,
            ids["tenant_a"],
            doc_id,
            vector,
        )

    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        row = await conn.fetchrow("SELECT embedding FROM chunks WHERE id = $1", chunk_id)

    assert row["embedding"].to_list() == pytest.approx(vector)


async def test_embedding_and_embedding_model_must_be_set_together(
    db_bootstrap_pool: asyncpg.Pool,
) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    doc_id = uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
            doc_id,
            ids["tenant_a"],
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
                "char_end, content, content_sha256, token_count, embedding_model) "
                "VALUES ($1, $2, $3, 'Intro', 0, 10, 'hello', 'h', 1, 'text-embedding-3-small')",
                uuid.uuid4(),
                ids["tenant_a"],
                doc_id,
            )


async def test_content_tsv_is_generated_from_content(db_bootstrap_pool: asyncpg.Pool) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    doc_id, chunk_id = uuid.uuid4(), uuid.uuid4()
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', 100, 'k')",
            doc_id,
            ids["tenant_a"],
        )
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count) "
            "VALUES ($1, $2, $3, 'Intro', 0, 20, 'quantum computing basics', 'h', 3)",
            chunk_id,
            ids["tenant_a"],
            doc_id,
        )
        row = await conn.fetchrow(
            "SELECT (content_tsv @@ to_tsquery('english', 'quantum')) AS matches "
            "FROM chunks WHERE id = $1",
            chunk_id,
        )
    assert row["matches"] is True


async def test_document_size_over_50mb_is_rejected(db_bootstrap_pool: asyncpg.Pool) -> None:
    ids = await _seed_two_tenants(db_bootstrap_pool)
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ids["tenant_a"]))
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
                "size_bytes, object_key) VALUES ($1, $2, 'f.pdf', 'x', 'application/pdf', "
                "52428801, 'k')",
                uuid.uuid4(),
                ids["tenant_a"],
            )

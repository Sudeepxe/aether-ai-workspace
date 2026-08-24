"""Real-Postgres + real-MinIO proof for the workspace-deletion saga
(issue #84, DF-3) — the literal acceptance criterion: a real workspace
with real memberships/threads/messages/citations/documents/chunks goes
through the full async saga (soft-delete -> outbox -> worker purge ->
hard-delete cascade) and ends with zero residual rows in every child
table and zero residual objects in MinIO, plus real per-store evidence
in both the surviving deletion_jobs row and the audit_events completion
event. A second test proves a mid-saga crash + outbox redelivery
completes without a double-purge error (idempotent retry).
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest

from aether.adapters.clock import SystemClock
from aether.adapters.idgen import Uuid7Generator
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.audit_log import PostgresAuditLog
from aether.adapters.postgres.deletion_job_repository import PostgresDeletionJobRepository
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.workspace_deletion_repository import (
    PostgresWorkspaceDeletionRepository,
)
from aether.adapters.postgres.workspace_repository import PostgresWorkspaceRepository
from aether.app.workspaces.delete_workspace import DeleteWorkspace, DeleteWorkspaceCommand
from aether.app.workspaces.purge_workspace import DispatchWorkspaceDeletion
from aether.domain.entities import DeletionJob

pytestmark = pytest.mark.integration


async def _seed_full_workspace(
    bootstrap_pool: asyncpg.Pool, object_storage: MinioObjectStorage
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """A real workspace with one of everything that FKs to it: a
    membership, a thread + assistant message, a ready document + chunk,
    and a citation linking the message to the chunk — plus a real
    object actually uploaded to MinIO under the document's key.
    Returns (workspace_id, user_id, object_key)."""
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    object_key = f"{workspace_id}/testdoc.md"
    content = b"Acme costs $10/mo."

    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Deletion Saga Test', $2)",
            workspace_id,
            f"deletion-saga-{workspace_id}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2, 'Test User')",
            user_id,
            f"{user_id}@example.com",
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        await conn.execute(
            "INSERT INTO memberships (id, workspace_id, user_id, role) VALUES ($1, $2, $3, 'owner')",
            uuid.uuid4(),
            workspace_id,
            user_id,
        )
        await conn.execute(
            "INSERT INTO threads (id, workspace_id, created_by) VALUES ($1, $2, $3)",
            thread_id,
            workspace_id,
            user_id,
        )
        await conn.execute(
            "INSERT INTO messages (id, workspace_id, thread_id, seq, role, content, status, "
            "grounded) VALUES ($1, $2, $3, 1, 'assistant', 'Acme costs $10/mo.', 'complete', true)",
            message_id,
            workspace_id,
            thread_id,
        )
        await conn.execute(
            "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
            "size_bytes, object_key, status) VALUES "
            "($1, $2, 'testdoc.md', 'x', 'text/markdown', $3, $4, 'ready')",
            document_id,
            workspace_id,
            len(content),
            object_key,
        )
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, document_id, section_path, char_start, "
            "char_end, content, content_sha256, token_count) VALUES "
            "($1, $2, $3, 'Pricing', 0, 20, 'Acme costs $10/mo.', 'h', 5)",
            chunk_id,
            workspace_id,
            document_id,
        )
        await conn.execute(
            "INSERT INTO message_citations (id, workspace_id, message_id, chunk_id, "
            "document_title, section_path, page_start, page_end) VALUES "
            "($1, $2, $3, $4, 'testdoc.md', 'Pricing', 1, 1)",
            uuid.uuid4(),
            workspace_id,
            message_id,
            chunk_id,
        )

    presigned = object_storage.presign_upload(
        key=object_key,
        content_type="text/markdown",
        max_size_bytes=len(content) + 10,
        expires_seconds=900,
    )
    files = {"file": ("testdoc.md", content, "text/markdown")}
    upload_resp = httpx.post(presigned.url, data=presigned.fields, files=files, timeout=10.0)
    assert upload_resp.status_code in (200, 204), upload_resp.text
    assert await object_storage.object_exists(key=object_key)

    return workspace_id, user_id, object_key


async def _request_deletion(
    db_pool: asyncpg.Pool, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> DeletionJob:
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        use_case = DeleteWorkspace(
            workspaces=PostgresWorkspaceRepository(conn),
            deletion_jobs=PostgresDeletionJobRepository(conn),
            outbox=PostgresOutboxRepository(conn),
            audit_log=PostgresAuditLog(conn),
            clock=SystemClock(),
            ids=Uuid7Generator(),
        )
        return await use_case.execute(
            DeleteWorkspaceCommand(workspace_id=workspace_id, actor_user_id=user_id)
        )


def _build_dispatcher(
    worker_db_pool: asyncpg.Pool, object_storage: MinioObjectStorage
) -> DispatchWorkspaceDeletion:
    return DispatchWorkspaceDeletion(
        outbox=PostgresOutboxRepository(worker_db_pool),
        repository=PostgresWorkspaceDeletionRepository(worker_db_pool),
        object_storage=object_storage,
        clock=SystemClock(),
        ids=Uuid7Generator(),
    )


async def test_the_full_saga_leaves_zero_residual_rows_and_objects(
    db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
) -> None:
    workspace_id, user_id, object_key = await _seed_full_workspace(
        db_bootstrap_pool, object_storage
    )
    job = await _request_deletion(db_pool, workspace_id=workspace_id, user_id=user_id)

    result = await _build_dispatcher(worker_db_pool, object_storage).execute()

    assert result.dispatched == 1
    assert result.failed == 0

    async with db_bootstrap_pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT count(*) FROM workspaces WHERE id = $1", workspace_id) == 0
        )
        for table in (
            "memberships",
            "threads",
            "messages",
            "message_citations",
            "documents",
            "chunks",
        ):
            # table is one of the fixed literals in the tuple above, never
            # user input — safe string interpolation, not injectable.
            residual = await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE workspace_id = $1",  # noqa: S608
                workspace_id,
            )
            assert residual == 0, f"{table} has {residual} residual row(s)"

        # deletion_jobs survives (no FK, by design) carrying real evidence.
        job_row = await conn.fetchrow(
            "SELECT status, evidence, completed_at FROM deletion_jobs WHERE id = $1", job.id
        )
        assert job_row is not None
        assert job_row["status"] == "complete"
        assert dict(job_row["evidence"]) == {"objects_purged": 1}
        assert job_row["completed_at"] is not None

        # The system-plane completion audit event survives too
        # (workspace_id=NULL — see the deletion_jobs migration's
        # docstring) with the same real evidence payload.
        audit_row = await conn.fetchrow(
            "SELECT workspace_id, metadata FROM audit_events "
            "WHERE action = 'workspace.deleted' AND target_id = $1",
            workspace_id,
        )
        assert audit_row is not None
        assert audit_row["workspace_id"] is None
        assert dict(audit_row["metadata"]) == {"objects_purged": 1}

    assert not await object_storage.object_exists(key=object_key)


async def test_a_mid_saga_crash_and_redelivery_completes_without_double_purge(
    db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
) -> None:
    workspace_id, user_id, object_key = await _seed_full_workspace(
        db_bootstrap_pool, object_storage
    )
    job = await _request_deletion(db_pool, workspace_id=workspace_id, user_id=user_id)
    dispatcher = _build_dispatcher(worker_db_pool, object_storage)

    first = await dispatcher.execute()
    assert first.dispatched == 1
    assert first.failed == 0

    # Simulate the crash window: repository.complete()'s transaction
    # landed (job complete, workspace hard-deleted, audit evidence
    # written) but the dispatcher's own outbox.mark_dispatched() never
    # committed — the outbox row is still eligible for redelivery.
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute(
            "UPDATE outbox SET dispatched_at = NULL "
            "WHERE event_type = 'workspace.delete_requested' AND aggregate_id = $1",
            workspace_id,
        )

    second = await dispatcher.execute()

    assert second.dispatched == 1
    assert second.failed == 0
    async with db_bootstrap_pool.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, evidence FROM deletion_jobs WHERE id = $1", job.id
        )
        assert job_row is not None
        assert job_row["status"] == "complete"
        # Still exactly the first pass's evidence — no double-purge,
        # no second (impossible, since documents is already gone)
        # object-listing attempt inflated the count.
        assert dict(job_row["evidence"]) == {"objects_purged": 1}
    assert not await object_storage.object_exists(key=object_key)


async def test_deletion_jobs_cannot_be_read_across_workspaces(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool, object_storage: MinioObjectStorage
) -> None:
    workspace_a, user_a, _ = await _seed_full_workspace(db_bootstrap_pool, object_storage)
    workspace_b, _, _ = await _seed_full_workspace(db_bootstrap_pool, object_storage)
    job = await _request_deletion(db_pool, workspace_id=workspace_a, user_id=user_a)

    # RLS, not just the repository's own explicit WHERE clause, must
    # block this: the connection's tenant context is workspace_b, but
    # the call itself claims the *correct* (workspace_a, job.id).
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_b))
        cross_tenant_row = await PostgresDeletionJobRepository(conn).get_by_id(workspace_a, job.id)

    assert cross_tenant_row is None

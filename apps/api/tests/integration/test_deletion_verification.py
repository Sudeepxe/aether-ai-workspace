"""Real-Postgres + real-MinIO proof for the deletion-verification saga
(issue #86, NFR-PR-1) — the literal §11.6 S8 exit criterion:
``test_FR_KB_5_deletion_cascades`` + the evidence job green.

A real workspace with one row in every tenant-scoped table (memberships,
threads, messages, citations, documents, chunks, feedback,
memory_summaries, invitations, usage_events, budgets) is deleted via
the real DF-3 saga (issue #84) end to end, then independently verified
by this job — a real, separate query sweep across every one of those
tables plus a real MinIO prefix listing, never trusting the deletion
saga's own self-reported success.

The second test is the acceptance criterion's other half: a
deliberately-broken deletion path (a stray object the purge step
"missed") proven to make the verifier fail loudly, then fixed and
re-verified — proving the verifier isn't a rubber stamp. FK constraints
on every DB table make an equivalent *row*-residue scenario physically
unreproducible against the real schema (the cascade that would leave a
row behind is exactly what the FK graph prevents) — object storage has
no such constraint, so it's the one dimension a real bug is actually
reproducible in; the row-residue detection logic itself is proven at
the unit level (test_verify_workspace_deletions.py), against a fake
that can report whatever a real bug might produce.
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
from aether.adapters.postgres.deletion_verification_repository import (
    PostgresDeletionVerificationRepository,
)
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.workspace_deletion_repository import (
    PostgresWorkspaceDeletionRepository,
)
from aether.adapters.postgres.workspace_repository import PostgresWorkspaceRepository
from aether.app.workspaces.delete_workspace import DeleteWorkspace, DeleteWorkspaceCommand
from aether.app.workspaces.purge_workspace import DispatchWorkspaceDeletion
from aether.app.workspaces.verify_deletions import VerifyWorkspaceDeletions
from aether.domain.entities import DeletionJob

pytestmark = pytest.mark.integration

_RESIDUE_TABLES = (
    "memberships",
    "threads",
    "messages",
    "message_citations",
    "documents",
    "chunks",
    "usage_events",
    "budgets",
    "invitations",
    "feedback",
    "memory_summaries",
)


async def _seed_full_workspace(
    bootstrap_pool: asyncpg.Pool, object_storage: MinioObjectStorage
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """One real row in every tenant-scoped table §86 names, plus a real
    object actually uploaded to MinIO. Returns
    (workspace_id, user_id, object_key)."""
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    message_id = uuid.uuid4()
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    object_key = f"{workspace_id}/pricing.md"
    content = b"Acme costs $10/mo."

    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'FR-KB-5 Test', $2)",
            workspace_id,
            f"fr-kb-5-{workspace_id}",
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
            "($1, $2, 'pricing.md', 'x', 'text/markdown', $3, $4, 'ready')",
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
            "($1, $2, $3, $4, 'pricing.md', 'Pricing', 1, 1)",
            uuid.uuid4(),
            workspace_id,
            message_id,
            chunk_id,
        )
        await conn.execute(
            "INSERT INTO feedback (id, workspace_id, message_id, user_id, rating) "
            "VALUES ($1, $2, $3, $4, 'up')",
            uuid.uuid4(),
            workspace_id,
            message_id,
            user_id,
        )
        await conn.execute(
            "INSERT INTO memory_summaries (id, workspace_id, thread_id, upto_seq, content, "
            "model, token_count) VALUES ($1, $2, $3, 1, 'condensed history', 'test-model', 3)",
            uuid.uuid4(),
            workspace_id,
            thread_id,
        )
        await conn.execute(
            "INSERT INTO invitations (id, workspace_id, email, role, token_hash, invited_by, "
            "expires_at) VALUES ($1, $2, 'invitee@example.com', 'member', $3, $4, "
            "now() + interval '7 days')",
            uuid.uuid4(),
            workspace_id,
            f"tok-{uuid.uuid4()}",
            user_id,
        )
        await conn.execute(
            "INSERT INTO budgets (workspace_id, monthly_limit_microcents, current_period_start) "
            "VALUES ($1, 500000000, CURRENT_DATE)",
            workspace_id,
        )
        await conn.execute(
            "INSERT INTO usage_events (id, workspace_id, user_id, kind, model, prompt_tokens, "
            "completion_tokens, cost_microcents) VALUES "
            "($1, $2, $3, 'chat', 'test-model', 10, 20, 100)",
            uuid.uuid4(),
            workspace_id,
            user_id,
        )

    presigned = object_storage.presign_upload(
        key=object_key,
        content_type="text/markdown",
        max_size_bytes=len(content) + 10,
        expires_seconds=900,
    )
    files = {"file": ("pricing.md", content, "text/markdown")}
    upload_resp = httpx.post(presigned.url, data=presigned.fields, files=files, timeout=10.0)
    assert upload_resp.status_code in (200, 204), upload_resp.text

    return workspace_id, user_id, object_key


async def _delete_and_purge(
    db_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> DeletionJob:
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        job = await DeleteWorkspace(
            workspaces=PostgresWorkspaceRepository(conn),
            deletion_jobs=PostgresDeletionJobRepository(conn),
            outbox=PostgresOutboxRepository(conn),
            audit_log=PostgresAuditLog(conn),
            clock=SystemClock(),
            ids=Uuid7Generator(),
        ).execute(DeleteWorkspaceCommand(workspace_id=workspace_id, actor_user_id=user_id))

    dispatcher = DispatchWorkspaceDeletion(
        outbox=PostgresOutboxRepository(worker_db_pool),
        repository=PostgresWorkspaceDeletionRepository(worker_db_pool),
        object_storage=object_storage,
        clock=SystemClock(),
        ids=Uuid7Generator(),
    )
    result = await dispatcher.execute()
    assert result.dispatched == 1
    assert result.failed == 0
    return job


def _build_verifier(
    worker_db_pool: asyncpg.Pool, object_storage: MinioObjectStorage, *, min_age_seconds: int = 0
) -> VerifyWorkspaceDeletions:
    return VerifyWorkspaceDeletions(
        repository=PostgresDeletionVerificationRepository(worker_db_pool),
        object_storage=object_storage,
        clock=SystemClock(),
        ids=Uuid7Generator(),
        min_age_seconds=min_age_seconds,
    )


async def test_FR_KB_5_deletion_cascades(  # noqa: N802 — the literal blueprint §11.6 test name
    db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
) -> None:
    workspace_id, user_id, object_key = await _seed_full_workspace(
        db_bootstrap_pool, object_storage
    )
    job = await _delete_and_purge(
        db_pool, worker_db_pool, object_storage, workspace_id=workspace_id, user_id=user_id
    )

    verify_result = await _build_verifier(worker_db_pool, object_storage).execute()

    assert verify_result.passed == 1
    assert verify_result.failed == 0

    async with db_bootstrap_pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT count(*) FROM workspaces WHERE id = $1", workspace_id) == 0
        )
        for table in _RESIDUE_TABLES:
            residual = await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE workspace_id = $1",  # noqa: S608
                workspace_id,
            )
            assert residual == 0, f"{table} has {residual} real residual row(s)"
        # audit_events: zero rows for THIS workspace_id specifically —
        # the completion/verification events are deliberately
        # workspace_id=NULL system-plane rows, not counted here.
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM audit_events WHERE workspace_id = $1", workspace_id
            )
            == 0
        )

        job_row = await conn.fetchrow(
            "SELECT verified_at, verification_passed, evidence FROM deletion_jobs WHERE id = $1",
            job.id,
        )
        assert job_row is not None
        assert job_row["verified_at"] is not None
        assert job_row["verification_passed"] is True
        assert dict(job_row["evidence"])["verification"]["passed"] is True

        verified_audit_row = await conn.fetchrow(
            "SELECT metadata FROM audit_events WHERE action = 'workspace.deletion_verified' "
            "AND target_id = $1",
            workspace_id,
        )
        assert verified_audit_row is not None
        assert dict(verified_audit_row["metadata"])["passed"] is True

    assert not await object_storage.object_exists(key=object_key)


async def test_a_stray_object_makes_verification_fail_then_pass_once_fixed(
    db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
) -> None:
    """The acceptance criterion's literal "deliberately-broken... proven
    to make the verifier fail loudly, then fixed" scenario, against
    real MinIO."""
    workspace_id, user_id, _ = await _seed_full_workspace(db_bootstrap_pool, object_storage)
    job = await _delete_and_purge(
        db_pool, worker_db_pool, object_storage, workspace_id=workspace_id, user_id=user_id
    )
    verifier = _build_verifier(worker_db_pool, object_storage)

    # Simulate a purge bug: a file the real saga's purge step missed,
    # re-uploaded to the same (now-orphaned) workspace prefix after
    # deletion completed.
    stray_key = f"{workspace_id}/stray-leftover.md"
    presigned = object_storage.presign_upload(
        key=stray_key,
        content_type="text/markdown",
        max_size_bytes=100,
        expires_seconds=900,
    )
    upload_resp = httpx.post(
        presigned.url,
        data=presigned.fields,
        files={"file": ("stray-leftover.md", b"oops", "text/markdown")},
        timeout=10.0,
    )
    assert upload_resp.status_code in (200, 204), upload_resp.text

    broken_report = await verifier.verify_job(job)

    assert broken_report.passed is False
    assert stray_key in broken_report.residual_object_keys
    async with db_bootstrap_pool.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT verification_passed FROM deletion_jobs WHERE id = $1", job.id
        )
        assert job_row is not None
        assert job_row["verification_passed"] is False

    # Fix it — the missed purge step, run by hand.
    await object_storage.delete(key=stray_key)

    fixed_report = await verifier.verify_job(job)

    assert fixed_report.passed is True
    async with db_bootstrap_pool.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT verification_passed FROM deletion_jobs WHERE id = $1", job.id
        )
        assert job_row is not None
        assert job_row["verification_passed"] is True


async def test_a_too_recent_completed_job_is_not_yet_eligible_for_the_scheduled_sweep(
    db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
) -> None:
    """Real proof of the min_age_seconds decoupling gate (issue #86:
    "not immediately inline") against real Postgres wall-clock time —
    the unit tests can't prove this since the fake doesn't implement
    real time-based filtering."""
    workspace_id, user_id, _ = await _seed_full_workspace(db_bootstrap_pool, object_storage)
    await _delete_and_purge(
        db_pool, worker_db_pool, object_storage, workspace_id=workspace_id, user_id=user_id
    )

    # A large min_age_seconds means "not old enough yet" — the sweep
    # must find nothing.
    patient_verifier = _build_verifier(worker_db_pool, object_storage, min_age_seconds=3600)
    result = await patient_verifier.execute()
    assert result.passed == 0
    assert result.failed == 0

    # min_age_seconds=0 immediately picks it up.
    result = await _build_verifier(worker_db_pool, object_storage, min_age_seconds=0).execute()
    assert result.passed == 1

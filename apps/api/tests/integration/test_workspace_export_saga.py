"""Real-Postgres + real-MinIO proof for the tenant-data-export saga
(issue #85, FR-AD-5) — the literal acceptance criterion: a real export
against a workspace with real documents/threads/messages, downloading
the resulting archive from real MinIO, and asserting its JSON contents
match the source data and its bundled files match the originals byte-
for-byte; plus a real-HTTP proof that only an Owner (not a Member) may
request one (§7.3's "workspace delete/export/transfer" row).
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Iterator

import asyncpg
import httpx
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

from aether.adapters.clock import SystemClock
from aether.adapters.idgen import Uuid7Generator
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.audit_log import PostgresAuditLog
from aether.adapters.postgres.export_job_repository import PostgresExportJobRepository
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.workspace_export_repository import (
    PostgresWorkspaceExportRepository,
)
from aether.app.workspaces.build_export import EXPORT_SCHEMA_VERSION, DispatchWorkspaceExport
from aether.app.workspaces.request_export import (
    RequestWorkspaceExport,
    RequestWorkspaceExportCommand,
)
from aether.config import get_settings
from aether.domain.entities import ExportJob

pytestmark = pytest.mark.integration


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


def _register_and_login(client: TestClient, email: str) -> str:
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "s3cret!!", "display_name": email},
    )
    resp = client.post("/v1/auth/login", json={"email": email, "password": "s3cret!!"})
    token: str = resp.json()["access_token"]
    return token


@pytest.fixture()
def app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,  # unused directly: flush-on-teardown isolates rate-limit buckets
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        with TestClient(create_app()) as client:
            yield client
    finally:
        get_settings.cache_clear()


async def _seed_full_workspace(
    bootstrap_pool: asyncpg.Pool, object_storage: MinioObjectStorage
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """A real workspace with a membership, a thread + assistant message
    + citation, a ready document + chunk, and feedback — plus a real
    object actually uploaded to MinIO under the document's key. Returns
    (workspace_id, owner_user_id, object_key)."""
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
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'Export Saga Test', $2)",
            workspace_id,
            f"export-saga-{workspace_id}",
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
            "INSERT INTO threads (id, workspace_id, created_by, title) VALUES ($1, $2, $3, 'Pricing')",
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


async def _request_export(
    db_pool: asyncpg.Pool, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> ExportJob:
    async with db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        use_case = RequestWorkspaceExport(
            export_jobs=PostgresExportJobRepository(conn),
            outbox=PostgresOutboxRepository(conn),
            audit_log=PostgresAuditLog(conn),
            ids=Uuid7Generator(),
        )
        return await use_case.execute(
            RequestWorkspaceExportCommand(workspace_id=workspace_id, actor_user_id=user_id)
        )


async def test_the_full_saga_produces_a_downloadable_archive_matching_the_source_data(
    db_pool: asyncpg.Pool,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    object_storage: MinioObjectStorage,
) -> None:
    workspace_id, user_id, _ = await _seed_full_workspace(db_bootstrap_pool, object_storage)
    job = await _request_export(db_pool, workspace_id=workspace_id, user_id=user_id)

    dispatcher = DispatchWorkspaceExport(
        outbox=PostgresOutboxRepository(worker_db_pool),
        repository=PostgresWorkspaceExportRepository(worker_db_pool),
        object_storage=object_storage,
        clock=SystemClock(),
        ids=Uuid7Generator(),
    )
    result = await dispatcher.execute()

    assert result.dispatched == 1
    assert result.failed == 0

    async with db_bootstrap_pool.acquire() as conn:
        job_row = await conn.fetchrow(
            "SELECT status, archive_object_key, evidence FROM export_jobs WHERE id = $1", job.id
        )
    assert job_row is not None
    assert job_row["status"] == "complete"
    archive_object_key = job_row["archive_object_key"]
    assert archive_object_key == f"exports/{workspace_id}/{job.id}.zip"
    evidence = dict(job_row["evidence"])
    assert evidence["threads"] == 1
    assert evidence["messages"] == 1
    assert evidence["documents"] == 1
    assert evidence["files_bundled"] == 1
    assert evidence["archive_size_bytes"] > 0

    archive_bytes = await object_storage.download(key=archive_object_key)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        export_json = json.loads(archive.read("export.json"))
        assert export_json["export_version"] == EXPORT_SCHEMA_VERSION
        assert export_json["workspace"]["name"] == "Export Saga Test"
        assert len(export_json["memberships"]) == 1
        assert export_json["memberships"][0]["role"] == "owner"
        [thread] = export_json["threads"]
        assert thread["title"] == "Pricing"
        [message] = thread["messages"]
        assert message["content"] == "Acme costs $10/mo."
        assert message["citations"][0]["document_title"] == "pricing.md"
        [document] = export_json["documents"]
        assert document["filename"] == "pricing.md"
        [feedback_entry] = export_json["feedback"]
        assert feedback_entry["rating"] == "up"

        # The bundled original matches the real uploaded bytes exactly.
        bundled = archive.read(document["archive_path"])
        assert bundled == b"Acme costs $10/mo."


async def test_only_the_owner_may_request_an_export_over_http(
    app_client: TestClient, db_bootstrap_pool: asyncpg.Pool
) -> None:
    owner_token = _register_and_login(app_client, "export-owner@example.com")
    ws_resp = app_client.post(
        "/v1/workspaces",
        json={"name": "Owner Export Workspace"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    workspace_id = ws_resp.json()["id"]

    member_token = _register_and_login(app_client, "export-member@example.com")
    member_login = app_client.post(
        "/v1/auth/login", json={"email": "export-member@example.com", "password": "s3cret!!"}
    )
    assert member_login.status_code == 200
    # Discover the member's user id via /v1/me, then seed a real 'member'
    # (not 'owner') membership row directly — mirroring the same
    # direct-bootstrap-seeding precedent this session already uses for
    # fixture preconditions RLS would otherwise gate off (e.g. citation/
    # feedback/deletion tests' _seed_* helpers).
    me_resp = app_client.get("/v1/me", headers={"Authorization": f"Bearer {member_token}"})
    member_user_id = uuid.UUID(me_resp.json()["id"])
    async with db_bootstrap_pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", workspace_id)
        await conn.execute(
            "INSERT INTO memberships (id, workspace_id, user_id, role) VALUES ($1, $2, $3, 'member')",
            uuid.uuid4(),
            uuid.UUID(workspace_id),
            member_user_id,
        )

    member_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}:export",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert member_resp.status_code == 403

    owner_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}:export",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert owner_resp.status_code == 202
    assert owner_resp.json()["status"] == "queued"

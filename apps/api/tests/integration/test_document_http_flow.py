"""End-to-end HTTP proof for issue #48 (§4.3, FR-KB-5): the real
document.uploaded producer (documents:initiate + upload + documents:confirm)
that issues #46/#47's DocumentProcessor payload contract has been
waiting on, driven through a real app, real Postgres/Redis/MinIO/
ClamAV — not fakes — all the way to a document landing in ``ready`` (or
``failed`` for a poisoned upload), and a real cascading DELETE proving
FR-KB-5's literal acceptance criterion: chunks + vectors are physically
gone, not just status-flagged.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator

import asyncpg
import httpx
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

from aether.adapters.clamav.scanner import ClamAvScanner
from aether.adapters.clock import SystemClock
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.redis.ingestion_queue import RedisIngestionQueue
from aether.app.ingestion.dispatch_outbox_to_queue import DispatchIngestionOutbox
from aether.app.ingestion.process_document import DocumentProcessor
from aether.config import get_settings

pytestmark = pytest.mark.integration


def _as_role_url(bootstrap_url: str, role: str, password: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://{role}:{password}@{hostpart}"


@pytest.fixture()
def app_client(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,
    minio_endpoint: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    endpoint, access_key, secret_key = minio_endpoint
    monkeypatch.setenv(
        "AETHER_DATABASE_URL", _as_role_url(postgres_url, "app_api", "app-api-dev-only")
    )
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    monkeypatch.setenv("AETHER_OBJECT_STORAGE_ENDPOINT", endpoint)
    monkeypatch.setenv("AETHER_OBJECT_STORAGE_ACCESS_KEY", access_key)
    monkeypatch.setenv("AETHER_OBJECT_STORAGE_SECRET_KEY", secret_key)
    monkeypatch.setenv("AETHER_OBJECT_STORAGE_SECURE", "false")
    monkeypatch.setenv("AETHER_OBJECT_STORAGE_BUCKET", "aether-test-documents")
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        get_settings.cache_clear()


def _register_and_login(client: TestClient, email: str) -> str:
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "s3cret!!", "display_name": email},
    )
    resp = client.post("/v1/auth/login", json={"email": email, "password": "s3cret!!"})
    token: str = resp.json()["access_token"]
    return token


def _upload_to_presigned(upload_url: str, upload_fields: dict[str, str], content: bytes) -> None:
    files = {"file": ("upload.bin", content, upload_fields.get("Content-Type", "text/plain"))}
    resp = httpx.post(upload_url, data=upload_fields, files=files, timeout=10.0)
    assert resp.status_code in (200, 204), resp.text


async def _relay_and_process(
    *,
    worker_db_pool: asyncpg.Pool,
    redis_client: redis_asyncio.Redis,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    """The worker-side half (issues #45-#47, already proven independently
    by their own test suites): relay the outbox row into the real
    per-tenant fair queue, then process the one message that lands with
    the real pipeline handler."""
    outbox = PostgresOutboxRepository(worker_db_pool)
    queue = RedisIngestionQueue(redis_client, consumer_name=f"test-{uuid.uuid4().hex[:8]}")
    dispatch_result = await DispatchIngestionOutbox(
        outbox=outbox, queue=queue, clock=SystemClock()
    ).execute()
    assert dispatch_result.dispatched == 1, dispatch_result

    message = await queue.claim_next()
    assert message is not None
    host, port = clamav_endpoint
    processor = DocumentProcessor(
        object_storage=object_storage,
        scanner=ClamAvScanner(host=host, port=port),
        repository=PostgresIngestionRepository(worker_db_pool),
        embedder=LocalHashEmbeddingAdapter(),
    )
    await processor(message)
    await queue.ack(message)


async def test_upload_confirm_and_real_pipeline_lands_a_clean_document_in_ready(
    app_client: TestClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    redis_client: redis_asyncio.Redis,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    token = _register_and_login(app_client, "owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    ws_resp = app_client.post("/v1/workspaces", json={"name": "Acme"}, headers=headers)
    workspace_id = ws_resp.json()["id"]

    content = (
        b"# Introduction\n\nThis document proves the real ingestion pipeline "
        b"end to end, driven entirely through the HTTP API.\n"
    )
    content_sha256 = hashlib.sha256(content).hexdigest()

    initiate_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/documents:initiate",
        json={
            "filename": "intro.md",
            "mime": "text/markdown",
            "size_bytes": len(content),
            "content_sha256": content_sha256,
        },
        headers=headers,
    )
    assert initiate_resp.status_code == 200, initiate_resp.text
    initiated = initiate_resp.json()
    _upload_to_presigned(initiated["upload_url"], initiated["upload_fields"], content)

    confirm_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/documents:confirm",
        json={
            "document_id": initiated["document_id"],
            "object_key": initiated["object_key"],
            "filename": "intro.md",
            "mime": "text/markdown",
            "size_bytes": len(content),
            "content_sha256": content_sha256,
        },
        headers=headers,
    )
    assert confirm_resp.status_code == 201, confirm_resp.text
    assert confirm_resp.json()["status"] == "queued"
    document_id = initiated["document_id"]

    async with db_bootstrap_pool.acquire() as conn:
        outbox_row = await conn.fetchrow(
            "SELECT tenant_id, payload FROM outbox WHERE event_type = 'document.uploaded' "
            "AND aggregate_id = $1",
            uuid.UUID(document_id),
        )
    assert outbox_row is not None
    assert str(outbox_row["tenant_id"]) == workspace_id
    assert outbox_row["payload"]["object_key"] == initiated["object_key"]

    await _relay_and_process(
        worker_db_pool=worker_db_pool,
        redis_client=redis_client,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )

    get_resp = app_client.get(
        f"/v1/workspaces/{workspace_id}/documents/{document_id}", headers=headers
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "ready"

    list_resp = app_client.get(f"/v1/workspaces/{workspace_id}/documents", headers=headers)
    assert any(d["id"] == document_id for d in list_resp.json()["items"])


async def test_upload_confirm_and_real_pipeline_rejects_a_poisoned_document(
    app_client: TestClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    redis_client: redis_asyncio.Redis,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    token = _register_and_login(app_client, "owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    ws_resp = app_client.post("/v1/workspaces", json={"name": "Acme"}, headers=headers)
    workspace_id = ws_resp.json()["id"]

    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    content_sha256 = hashlib.sha256(eicar).hexdigest()

    initiate_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/documents:initiate",
        json={
            "filename": "eicar.txt",
            "mime": "text/plain",
            "size_bytes": len(eicar),
            "content_sha256": content_sha256,
        },
        headers=headers,
    )
    initiated = initiate_resp.json()
    _upload_to_presigned(initiated["upload_url"], initiated["upload_fields"], eicar)

    confirm_resp = app_client.post(
        f"/v1/workspaces/{workspace_id}/documents:confirm",
        json={
            "document_id": initiated["document_id"],
            "object_key": initiated["object_key"],
            "filename": "eicar.txt",
            "mime": "text/plain",
            "size_bytes": len(eicar),
            "content_sha256": content_sha256,
        },
        headers=headers,
    )
    assert confirm_resp.status_code == 201, confirm_resp.text
    document_id = initiated["document_id"]

    await _relay_and_process(
        worker_db_pool=worker_db_pool,
        redis_client=redis_client,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )

    get_resp = app_client.get(
        f"/v1/workspaces/{workspace_id}/documents/{document_id}", headers=headers
    )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["status"] == "failed"
    assert body["failure_stage"] == "scanning"
    assert "eicar" in body["failure_reason"].lower()


async def test_delete_physically_removes_chunks_and_vectors_not_just_a_status_flag(
    app_client: TestClient,
    db_bootstrap_pool: asyncpg.Pool,
    worker_db_pool: asyncpg.Pool,
    redis_client: redis_asyncio.Redis,
    object_storage: MinioObjectStorage,
    clamav_endpoint: tuple[str, int],
) -> None:
    """FR-KB-5's literal acceptance criterion."""
    token = _register_and_login(app_client, "owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    ws_resp = app_client.post("/v1/workspaces", json={"name": "Acme"}, headers=headers)
    workspace_id = ws_resp.json()["id"]

    content = b"# Title\n\nSome real content that will really get chunked and embedded.\n"
    content_sha256 = hashlib.sha256(content).hexdigest()
    initiated = app_client.post(
        f"/v1/workspaces/{workspace_id}/documents:initiate",
        json={
            "filename": "f.md",
            "mime": "text/markdown",
            "size_bytes": len(content),
            "content_sha256": content_sha256,
        },
        headers=headers,
    ).json()
    _upload_to_presigned(initiated["upload_url"], initiated["upload_fields"], content)
    app_client.post(
        f"/v1/workspaces/{workspace_id}/documents:confirm",
        json={
            "document_id": initiated["document_id"],
            "object_key": initiated["object_key"],
            "filename": "f.md",
            "mime": "text/markdown",
            "size_bytes": len(content),
            "content_sha256": content_sha256,
        },
        headers=headers,
    )
    document_id = initiated["document_id"]
    await _relay_and_process(
        worker_db_pool=worker_db_pool,
        redis_client=redis_client,
        object_storage=object_storage,
        clamav_endpoint=clamav_endpoint,
    )

    async with db_bootstrap_pool.acquire() as conn:
        chunk_count_before = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", uuid.UUID(document_id)
        )
    assert chunk_count_before >= 1  # sanity: there really is content to delete

    delete_resp = app_client.delete(
        f"/v1/workspaces/{workspace_id}/documents/{document_id}", headers=headers
    )
    assert delete_resp.status_code == 204, delete_resp.text

    async with db_bootstrap_pool.acquire() as conn:
        chunk_count_after = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", uuid.UUID(document_id)
        )
        doc_row = await conn.fetchrow(
            "SELECT deleted_at, status FROM documents WHERE id = $1", uuid.UUID(document_id)
        )
    assert chunk_count_after == 0  # physically gone, not just status-flagged
    assert doc_row["deleted_at"] is not None
    assert doc_row["status"] == "ready"  # the pipeline status itself is untouched by deletion

    get_resp = app_client.get(
        f"/v1/workspaces/{workspace_id}/documents/{document_id}", headers=headers
    )
    assert get_resp.status_code == 404

    second_delete = app_client.delete(
        f"/v1/workspaces/{workspace_id}/documents/{document_id}", headers=headers
    )
    assert second_delete.status_code == 404


async def test_list_documents_paginates_with_a_cursor(
    app_client: TestClient, db_bootstrap_pool: asyncpg.Pool
) -> None:
    token = _register_and_login(app_client, "owner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    workspace_id = app_client.post("/v1/workspaces", json={"name": "Acme"}, headers=headers).json()[
        "id"
    ]

    for i in range(3):
        content = f"# Doc {i}\n\nbody".encode()
        content_sha256 = hashlib.sha256(content).hexdigest()
        initiated = app_client.post(
            f"/v1/workspaces/{workspace_id}/documents:initiate",
            json={
                "filename": f"f{i}.md",
                "mime": "text/markdown",
                "size_bytes": len(content),
                "content_sha256": content_sha256,
            },
            headers=headers,
        ).json()
        # Not actually uploaded — :confirm would 409 without a real
        # object; this test only exercises list pagination against
        # seeded rows, so seed directly via the bootstrap connection.
        async with db_bootstrap_pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", workspace_id)
            await conn.execute(
                "INSERT INTO documents (id, workspace_id, filename, content_sha256, mime, "
                "size_bytes, object_key) VALUES ($1, $2, $3, $4, 'text/markdown', $5, $6)",
                uuid.UUID(initiated["document_id"]),
                uuid.UUID(workspace_id),
                f"f{i}.md",
                content_sha256,
                len(content),
                initiated["object_key"],
            )

    first_page = app_client.get(
        f"/v1/workspaces/{workspace_id}/documents?limit=2", headers=headers
    ).json()
    assert len(first_page["items"]) == 2
    assert first_page["next_cursor"] is not None

    second_page = app_client.get(
        f"/v1/workspaces/{workspace_id}/documents?limit=2&cursor={first_page['next_cursor']}",
        headers=headers,
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None

    seen_ids = {d["id"] for d in first_page["items"]} | {d["id"] for d in second_page["items"]}
    assert len(seen_ids) == 3

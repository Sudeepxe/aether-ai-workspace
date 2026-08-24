"""Worker-process composition root — the worker's counterpart to
http/composition.py. Same discipline: this is the one place in the
worker process allowed to import adapters directly (Blueprint §3.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import asyncpg
import redis.asyncio as redis_asyncio

from aether.adapters.clamav.scanner import ClamAvScanner
from aether.adapters.clock import SystemClock
from aether.adapters.email.resend import ResendEmailAdapter
from aether.adapters.email.smtp import SmtpEmailAdapter
from aether.adapters.idgen import Uuid7Generator
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.openai.embedding import OpenAiEmbeddingAdapter
from aether.adapters.postgres.deletion_verification_repository import (
    PostgresDeletionVerificationRepository,
)
from aether.adapters.postgres.ingestion_repository import PostgresIngestionRepository
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.pool import create_pool
from aether.adapters.postgres.workspace_deletion_repository import (
    PostgresWorkspaceDeletionRepository,
)
from aether.adapters.postgres.workspace_export_repository import (
    PostgresWorkspaceExportRepository,
)
from aether.adapters.redis.ingestion_queue import RedisIngestionQueue
from aether.app.ingestion.dispatch_outbox_to_queue import DispatchIngestionOutbox
from aether.app.ingestion.process_document import DocumentProcessor
from aether.app.notifications.dispatch_email_outbox import DispatchEmailOutbox
from aether.app.workspaces.build_export import DispatchWorkspaceExport
from aether.app.workspaces.purge_workspace import DispatchWorkspaceDeletion
from aether.app.workspaces.verify_deletions import VerifyWorkspaceDeletions
from aether.config import Settings
from aether.ports.deletion_verification import DeletionVerificationPort
from aether.ports.email import EmailPort
from aether.ports.embedding import EmbeddingProviderPort
from aether.ports.ingestion_queue import IngestionQueuePort
from aether.ports.ingestion_repository import IngestionRepositoryPort
from aether.ports.malware_scan import MalwareScanPort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.security import ClockPort, IdPort
from aether.ports.storage import ObjectStoragePort
from aether.ports.workspace_deletion import WorkspaceDeletionPort
from aether.ports.workspace_export import WorkspaceExportPort

# A real, if deliberately small, "not immediately inline" decoupling
# gate (issue #86) — this system has no read replicas to actually wait
# out lag on, so this is a token wait rather than a load-bearing one;
# the real safety property is that verification is a *separate* call
# from the deletion dispatcher, not that it's delayed by any particular
# amount.
_MIN_VERIFICATION_AGE_SECONDS = 60


@dataclass
class WorkerContainer:
    db_pool: asyncpg.Pool
    redis_client: redis_asyncio.Redis
    outbox: OutboxRepositoryPort
    email: EmailPort
    clock: ClockPort
    ingestion_queue: IngestionQueuePort
    object_storage: ObjectStoragePort
    scanner: MalwareScanPort
    ingestion_repository: IngestionRepositoryPort
    embedder: EmbeddingProviderPort
    ids: IdPort
    workspace_deletion_repository: WorkspaceDeletionPort
    workspace_export_repository: WorkspaceExportPort
    deletion_verification_repository: DeletionVerificationPort
    dispatch_email_outbox: DispatchEmailOutbox
    dispatch_ingestion_outbox: DispatchIngestionOutbox
    dispatch_workspace_deletion: DispatchWorkspaceDeletion
    dispatch_workspace_export: DispatchWorkspaceExport
    verify_workspace_deletions: VerifyWorkspaceDeletions
    process_document: DocumentProcessor
    """The real ingestion-pipeline handler (issue #46), passed to
    app.ingestion.consume_queue.run_ingestion_consumer by workers/main.py
    to actually start consuming — this container only builds it."""

    async def aclose(self) -> None:
        await self.db_pool.close()
        await self.redis_client.aclose()


def _build_email_adapter(settings: Settings) -> EmailPort:
    if settings.email_provider == "resend":
        return ResendEmailAdapter(api_key=settings.resend_api_key, sender=settings.email_sender)
    return SmtpEmailAdapter(
        host=settings.smtp_host, port=settings.smtp_port, sender=settings.email_sender
    )


def _build_embedder(settings: Settings) -> EmbeddingProviderPort:
    """Real OpenAI embeddings only if configured (§3.2.4/§6.2's D6-3
    pattern, mirroring http/composition.py's ``_build_generator``) —
    dev/CI environments without a SOPS-decrypted API key fall back to
    LocalHashEmbeddingAdapter, a real and honest (if non-semantic)
    embedder, not a silent stub."""
    if settings.openai_api_key:
        return OpenAiEmbeddingAdapter(api_key=settings.openai_api_key)
    return LocalHashEmbeddingAdapter()


async def build_worker_container(settings: Settings) -> WorkerContainer:
    db_pool = await create_pool(settings.database_worker_url)
    redis_client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]  # redis-py gap, not ours
        settings.redis_url, decode_responses=True
    )
    outbox = PostgresOutboxRepository(db_pool)
    email = _build_email_adapter(settings)
    clock = SystemClock()
    # One consumer name per worker process — distinct identities matter
    # for Redis Streams consumer-group bookkeeping (XPENDING/XCLAIM
    # attribute in-flight messages to a specific consumer), even though
    # this dispatcher's own enqueue() calls don't read as a consumer.
    ingestion_queue = RedisIngestionQueue(
        redis_client, consumer_name=f"worker-{uuid.uuid4().hex[:8]}"
    )
    object_storage = MinioObjectStorage(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        secure=settings.object_storage_secure,
        bucket=settings.object_storage_bucket,
    )
    scanner = ClamAvScanner(host=settings.clamav_host, port=settings.clamav_port)
    ingestion_repository = PostgresIngestionRepository(db_pool)
    embedder = _build_embedder(settings)
    ids = Uuid7Generator()
    workspace_deletion_repository = PostgresWorkspaceDeletionRepository(db_pool)
    workspace_export_repository = PostgresWorkspaceExportRepository(db_pool)
    deletion_verification_repository = PostgresDeletionVerificationRepository(db_pool)

    return WorkerContainer(
        db_pool=db_pool,
        redis_client=redis_client,
        outbox=outbox,
        email=email,
        clock=clock,
        ingestion_queue=ingestion_queue,
        object_storage=object_storage,
        scanner=scanner,
        ingestion_repository=ingestion_repository,
        embedder=embedder,
        ids=ids,
        workspace_deletion_repository=workspace_deletion_repository,
        workspace_export_repository=workspace_export_repository,
        deletion_verification_repository=deletion_verification_repository,
        dispatch_email_outbox=DispatchEmailOutbox(outbox=outbox, email=email, clock=clock),
        dispatch_ingestion_outbox=DispatchIngestionOutbox(
            outbox=outbox, queue=ingestion_queue, clock=clock
        ),
        dispatch_workspace_deletion=DispatchWorkspaceDeletion(
            outbox=outbox,
            repository=workspace_deletion_repository,
            object_storage=object_storage,
            clock=clock,
            ids=ids,
        ),
        dispatch_workspace_export=DispatchWorkspaceExport(
            outbox=outbox,
            repository=workspace_export_repository,
            object_storage=object_storage,
            clock=clock,
            ids=ids,
        ),
        verify_workspace_deletions=VerifyWorkspaceDeletions(
            repository=deletion_verification_repository,
            object_storage=object_storage,
            clock=clock,
            ids=ids,
            min_age_seconds=_MIN_VERIFICATION_AGE_SECONDS,
        ),
        process_document=DocumentProcessor(
            object_storage=object_storage,
            scanner=scanner,
            repository=ingestion_repository,
            embedder=embedder,
        ),
    )

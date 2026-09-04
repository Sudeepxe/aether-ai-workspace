"""Composition root — the one place allowed to import adapters directly
and wire them to ports (Blueprint §3.3: "nothing imports adapters except
the composition root"). Constructed once at process startup (see
``http/app.py``'s lifespan) and threaded through FastAPI's dependency
system via ``request.app.state.container``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg
import redis.asyncio as redis_asyncio

from aether.adapters.anthropic.completion import AnthropicCompletionAdapter
from aether.adapters.argon2.hasher import Argon2PasswordHasher
from aether.adapters.clock import SystemClock
from aether.adapters.echo.generator import EchoGenerator
from aether.adapters.groq.completion import GroqCompletionAdapter
from aether.adapters.idgen import Uuid7Generator
from aether.adapters.jwt.eddsa import EdDSATokenSigner
from aether.adapters.llm.memory_compaction import LlmMemoryCompactionAdapter
from aether.adapters.llm.query_rewrite import LlmQueryRewriteAdapter
from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.local.noop_query_rewrite import NoOpQueryRewriteAdapter
from aether.adapters.local.truncating_memory_compaction import (
    MODEL_NAME as TRUNCATING_MEMORY_COMPACTION_MODEL_NAME,
)
from aether.adapters.local.truncating_memory_compaction import TruncatingMemoryCompactionAdapter
from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.openai.completion import OpenAiCompletionAdapter
from aether.adapters.openai.embedding import OpenAiEmbeddingAdapter
from aether.adapters.postgres.api_key_repository import PostgresApiKeyRepository
from aether.adapters.postgres.audit_log import PostgresAuditLog
from aether.adapters.postgres.budget_repository import PostgresBudgetRepository
from aether.adapters.postgres.chunk_search import PooledChunkSearch
from aether.adapters.postgres.citation_repository import PostgresCitationRepository
from aether.adapters.postgres.deletion_job_repository import PostgresDeletionJobRepository
from aether.adapters.postgres.document_repository import PostgresDocumentRepository
from aether.adapters.postgres.export_job_repository import PostgresExportJobRepository
from aether.adapters.postgres.feedback_repository import PostgresFeedbackRepository
from aether.adapters.postgres.invitation_repository import PostgresInvitationRepository
from aether.adapters.postgres.membership_repository import PostgresMembershipRepository
from aether.adapters.postgres.memory_summary_store import PostgresMemorySummaryStore
from aether.adapters.postgres.message_repository import PostgresMessageRepository
from aether.adapters.postgres.message_store import PostgresMessageStore
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.password_reset_token_repository import (
    PostgresPasswordResetTokenRepository,
)
from aether.adapters.postgres.pool import create_pool
from aether.adapters.postgres.refresh_token_repository import PostgresRefreshTokenRepository
from aether.adapters.postgres.thread_repository import PostgresThreadRepository
from aether.adapters.postgres.usage_ledger import PostgresBudgetAdmission, PostgresUsageLedger
from aether.adapters.postgres.user_repository import PostgresUserRepository
from aether.adapters.postgres.workspace_repository import PostgresWorkspaceRepository
from aether.adapters.redis.cancellation import RedisCancellationChannel
from aether.adapters.redis.denylist import RedisJtiDenylist
from aether.adapters.redis.idempotency import RedisIdempotencyStore
from aether.adapters.redis.rate_limiter import (
    FailOpenRateLimiter,
    LocalTokenBucketRateLimiter,
    RedisTokenBucketRateLimiter,
)
from aether.adapters.redis.stream_buffer import RedisStreamBuffer
from aether.app.api_keys.create_api_key import CreateApiKey
from aether.app.api_keys.list_api_keys import ListApiKeys
from aether.app.api_keys.revoke_api_key import RevokeApiKey
from aether.app.api_keys.verify_api_key import VerifyApiKey
from aether.app.auth.login_user import LoginUser
from aether.app.auth.logout_user import LogoutUser
from aether.app.auth.refresh_session import RefreshSession
from aether.app.auth.register_user import RegisterUser
from aether.app.auth.revoke_user_sessions import RevokeUserSessions
from aether.app.chat.cancel_generation import CancelGeneration
from aether.app.chat.get_generation_status import GetGenerationStatus
from aether.app.chat.memory_assembly import MemoryAssembler
from aether.app.chat.send_message import SendMessage
from aether.app.documents.confirm_upload import ConfirmDocumentUpload
from aether.app.documents.delete_document import DeleteDocument
from aether.app.documents.get_document import GetDocument
from aether.app.documents.initiate_upload import InitiateDocumentUpload
from aether.app.documents.list_documents import ListDocuments
from aether.app.invitations.accept_invitation import AcceptInvitation
from aether.app.invitations.create_invitation import CreateInvitation
from aether.app.invitations.revoke_invitation import RevokeInvitation
from aether.app.llm.circuit_breaker import CircuitBreaker
from aether.app.llm.router import LlmRouter
from aether.app.metering.get_budget import GetBudget
from aether.app.metering.get_usage import GetUsage
from aether.app.metering.update_budget import UpdateBudget
from aether.app.password_reset.request_password_reset import RequestPasswordReset
from aether.app.password_reset.reset_password import ResetPassword
from aether.app.retrieval.hybrid_search import HybridSearch
from aether.app.retrieval.query_rewrite import QueryRewriter
from aether.app.retrieval.refusal_gate import RetrievalGate
from aether.app.threads.create_thread import CreateThread
from aether.app.threads.delete_thread import DeleteThread
from aether.app.threads.get_thread import GetThread
from aether.app.threads.list_messages import ListMessages
from aether.app.threads.list_threads import ListThreads
from aether.app.threads.submit_feedback import SubmitFeedback
from aether.app.threads.update_thread import UpdateThread
from aether.app.workspaces.create_workspace import CreateWorkspace
from aether.app.workspaces.delete_workspace import DeleteWorkspace
from aether.app.workspaces.get_deletion_job import GetDeletionJob
from aether.app.workspaces.get_export_job import GetExportJob
from aether.app.workspaces.get_workspace import GetWorkspace
from aether.app.workspaces.manage_members import ListMembers, RemoveMember, UpdateMemberRole
from aether.app.workspaces.request_export import RequestWorkspaceExport
from aether.app.workspaces.update_workspace import UpdateWorkspace
from aether.config import Settings
from aether.ports.audit import AuditLogPort
from aether.ports.chat import GeneratorPort, MessageStorePort
from aether.ports.embedding import EmbeddingProviderPort
from aether.ports.idempotency import IdempotencyStorePort
from aether.ports.llm import ProviderAdapterPort
from aether.ports.memory import MemoryCompactionPort
from aether.ports.metering import BudgetAdmissionPort, BudgetRepositoryPort, UsageLedgerPort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.query_rewrite import QueryRewritePort
from aether.ports.rate_limit import RateLimitPort
from aether.ports.repositories import (
    ApiKeyRepositoryPort,
    CitationRepositoryPort,
    DeletionJobRepositoryPort,
    DocumentRepositoryPort,
    ExportJobRepositoryPort,
    FeedbackRepositoryPort,
    InvitationRepositoryPort,
    Membership,
    MembershipRepositoryPort,
    MessageRepositoryPort,
    PasswordResetTokenRepositoryPort,
    RefreshTokenRepositoryPort,
    ThreadRepositoryPort,
    UserRepositoryPort,
    WorkspaceRepositoryPort,
)
from aether.ports.revocation import RevocationPort
from aether.ports.security import ClockPort, IdPort, PasswordHasherPort, TokenPort
from aether.ports.storage import ObjectStoragePort
from aether.ports.streaming import CancellationPort, StreamBufferPort


@dataclass
class Container:
    db_pool: asyncpg.Pool
    redis_client: redis_asyncio.Redis
    env: str
    """Threaded into CreateApiKey — the ``aeth_{env}_...`` key-format
    segment (§7.4) — not stored anywhere else in Container since nothing
    before API keys needed the raw env string at the composition layer."""

    users: UserRepositoryPort
    refresh_tokens: RefreshTokenRepositoryPort
    invitations: InvitationRepositoryPort
    """Pool-bound, not connection-scoped: invitations is RLS-exempt (see
    its migration), so unlike workspaces/memberships it needs no
    per-request tenant context — this is the instance used for the
    accept-by-token lookup, which by definition runs before any tenant
    scope is known. See http/deps.py's get_invitation_acceptance_scope."""
    api_keys: ApiKeyRepositoryPort
    """Pool-bound, RLS-exempt (same reason as invitations) — the
    instance used for verify_api_key's global by-prefix lookup, which by
    definition runs before any tenant scope is known."""
    verify_api_key: VerifyApiKey
    idempotency_store: IdempotencyStorePort
    """Pool-bound (well, Redis-client-bound) singleton — ADR-4.6's
    generic Idempotency-Key replay store, consumed by
    http/idempotency.py's ``idempotency_guard``/``IdempotencyAwareRoute``."""
    password_reset_tokens: PasswordResetTokenRepositoryPort
    hasher: PasswordHasherPort
    tokens: TokenPort
    clock: ClockPort
    ids: IdPort
    revocations: RevocationPort
    audit_log: AuditLogPort
    """Pool-bound, used for auth-plane (workspace_id=None) events only —
    see adapters/postgres/audit_log.py's docstring for why that's safe
    without per-request tenant scoping."""
    outbox: OutboxRepositoryPort
    rate_limiter: RateLimitPort
    message_store: MessageStorePort
    """Pool-bound, short-transaction-per-call — see ports.chat.MessageStorePort's
    docstring for why chat persistence can't use the WorkspaceScope.conn
    pattern (a streaming request's lifetime would hold a connection open
    for the whole generation)."""
    generator: GeneratorPort
    stream_buffer: StreamBufferPort
    cancellation: CancellationPort
    budget_admission: BudgetAdmissionPort
    usage_ledger: UsageLedgerPort
    """Both pool-bound — see ports.metering's module docstring. Settlement
    (usage_ledger.record) is called directly by SendMessage after a
    successful generation, not via a separate worker consumer — see
    adapters.postgres.usage_ledger's module docstring for why per-event,
    same-request settlement is still correct."""
    object_storage: ObjectStoragePort
    embedder: EmbeddingProviderPort
    """Query-side embedding for hybrid retrieval (issue #56) — the API
    process's own instance, distinct from the worker's (issue #47):
    same real-OpenAI-if-configured/local-hash-fallback-otherwise
    pattern, but embedding a query is a synchronous request-path
    concern, not a pipeline-stage one."""
    query_rewriter: QueryRewritePort
    """Real cheap-model rewrite if a provider key is configured, else
    NoOpQueryRewriteAdapter (issue #57) — same honest-fallback pattern
    as embedder/generator."""

    register_user: RegisterUser
    login_user: LoginUser
    refresh_session: RefreshSession
    logout_user: LogoutUser
    revoke_user_sessions: RevokeUserSessions
    request_password_reset: RequestPasswordReset
    reset_password: ResetPassword
    send_message: SendMessage
    cancel_generation: CancelGeneration
    get_generation_status: GetGenerationStatus
    get_usage: GetUsage

    refresh_ttl_seconds: int
    default_workspace_monthly_budget_microcents: int
    default_budget_soft_pct: int

    async def aclose(self) -> None:
        await self.db_pool.close()
        await self.redis_client.aclose()


@dataclass
class WorkspaceScope:
    """Per-request, tenant-scoped composition — built fresh for every
    workspace-scoped request by http/deps.py's get_workspace_scope, bound
    to a connection that already has ``app.tenant_id`` set for the
    request's lifetime (one transaction, committed/rolled back when the
    request ends). Never held longer than one request; never shared
    across requests, unlike the singleton Container.
    """

    conn: asyncpg.Connection
    caller_membership: Membership

    workspaces: WorkspaceRepositoryPort
    deletion_jobs: DeletionJobRepositoryPort
    export_jobs: ExportJobRepositoryPort
    memberships: MembershipRepositoryPort
    invitations: InvitationRepositoryPort
    api_keys: ApiKeyRepositoryPort
    threads: ThreadRepositoryPort
    messages: MessageRepositoryPort
    citations: CitationRepositoryPort
    feedback: FeedbackRepositoryPort
    documents: DocumentRepositoryPort
    budgets: BudgetRepositoryPort
    audit_log: AuditLogPort
    outbox: OutboxRepositoryPort

    get_workspace: GetWorkspace
    update_workspace: UpdateWorkspace
    delete_workspace: DeleteWorkspace
    get_deletion_job: GetDeletionJob
    request_export: RequestWorkspaceExport
    get_export_job: GetExportJob
    list_members: ListMembers
    update_member_role: UpdateMemberRole
    remove_member: RemoveMember
    create_invitation: CreateInvitation
    revoke_invitation: RevokeInvitation
    create_api_key: CreateApiKey
    list_api_keys: ListApiKeys
    revoke_api_key: RevokeApiKey
    create_thread: CreateThread
    get_thread: GetThread
    list_threads: ListThreads
    update_thread: UpdateThread
    delete_thread: DeleteThread
    list_messages: ListMessages
    submit_feedback: SubmitFeedback
    get_budget: GetBudget
    update_budget: UpdateBudget
    initiate_document_upload: InitiateDocumentUpload
    confirm_document_upload: ConfirmDocumentUpload
    list_documents: ListDocuments
    get_document: GetDocument
    delete_document: DeleteDocument


def build_workspace_scope(
    conn: asyncpg.Connection,
    caller_membership: Membership,
    *,
    clock: ClockPort,
    ids: IdPort,
    object_storage: ObjectStoragePort,
    env: str,
) -> WorkspaceScope:
    workspaces = PostgresWorkspaceRepository(conn)
    deletion_jobs = PostgresDeletionJobRepository(conn)
    export_jobs = PostgresExportJobRepository(conn)
    memberships = PostgresMembershipRepository(conn)
    invitations = PostgresInvitationRepository(conn)
    api_keys = PostgresApiKeyRepository(conn)
    threads = PostgresThreadRepository(conn)
    messages = PostgresMessageRepository(conn)
    citations = PostgresCitationRepository(conn)
    feedback = PostgresFeedbackRepository(conn)
    documents = PostgresDocumentRepository(conn)
    budgets = PostgresBudgetRepository(conn)
    audit_log = PostgresAuditLog(conn)
    outbox = PostgresOutboxRepository(conn)
    return WorkspaceScope(
        conn=conn,
        caller_membership=caller_membership,
        workspaces=workspaces,
        deletion_jobs=deletion_jobs,
        export_jobs=export_jobs,
        memberships=memberships,
        invitations=invitations,
        api_keys=api_keys,
        threads=threads,
        messages=messages,
        citations=citations,
        feedback=feedback,
        documents=documents,
        budgets=budgets,
        audit_log=audit_log,
        outbox=outbox,
        get_workspace=GetWorkspace(workspaces=workspaces),
        update_workspace=UpdateWorkspace(workspaces=workspaces, audit_log=audit_log, ids=ids),
        delete_workspace=DeleteWorkspace(
            workspaces=workspaces,
            deletion_jobs=deletion_jobs,
            outbox=outbox,
            audit_log=audit_log,
            clock=clock,
            ids=ids,
        ),
        get_deletion_job=GetDeletionJob(deletion_jobs=deletion_jobs),
        request_export=RequestWorkspaceExport(
            export_jobs=export_jobs, outbox=outbox, audit_log=audit_log, ids=ids
        ),
        get_export_job=GetExportJob(export_jobs=export_jobs, object_storage=object_storage),
        list_members=ListMembers(memberships=memberships),
        update_member_role=UpdateMemberRole(memberships=memberships, audit_log=audit_log, ids=ids),
        remove_member=RemoveMember(memberships=memberships, audit_log=audit_log, ids=ids),
        create_invitation=CreateInvitation(
            invitations=invitations, audit_log=audit_log, outbox=outbox, clock=clock, ids=ids
        ),
        revoke_invitation=RevokeInvitation(invitations=invitations, audit_log=audit_log, ids=ids),
        create_api_key=CreateApiKey(api_keys=api_keys, audit_log=audit_log, ids=ids, env=env),
        list_api_keys=ListApiKeys(api_keys=api_keys),
        revoke_api_key=RevokeApiKey(api_keys=api_keys, audit_log=audit_log, clock=clock, ids=ids),
        create_thread=CreateThread(threads=threads, ids=ids),
        get_thread=GetThread(threads=threads),
        list_threads=ListThreads(threads=threads),
        update_thread=UpdateThread(threads=threads),
        delete_thread=DeleteThread(threads=threads, clock=clock),
        list_messages=ListMessages(messages=messages, citations=citations, feedback=feedback),
        submit_feedback=SubmitFeedback(messages=messages, feedback=feedback, ids=ids),
        get_budget=GetBudget(budgets=budgets),
        update_budget=UpdateBudget(budgets=budgets, audit_log=audit_log, ids=ids),
        initiate_document_upload=InitiateDocumentUpload(object_storage=object_storage, ids=ids),
        confirm_document_upload=ConfirmDocumentUpload(
            documents=documents, object_storage=object_storage, outbox=outbox, ids=ids
        ),
        list_documents=ListDocuments(documents=documents),
        get_document=GetDocument(documents=documents),
        delete_document=DeleteDocument(documents=documents, outbox=outbox, clock=clock, ids=ids),
    )


async def resolve_workspace_scope(
    conn: asyncpg.Connection,
    workspace_id: UUID,
    user_id: UUID,
    *,
    clock: ClockPort,
    ids: IdPort,
    object_storage: ObjectStoragePort,
    env: str,
) -> WorkspaceScope | None:
    """Looks up the caller's membership under ``conn`` (which must already
    have ``app.tenant_id`` set to ``workspace_id`` — see
    http/deps.py's get_workspace_scope) and builds the scope if found.
    None means "no membership row visible" — the caller is responsible
    for turning that into the same 404 used for "workspace doesn't
    exist" (§3.7.1: no existence oracle for cross-tenant probes)."""
    memberships = PostgresMembershipRepository(conn)
    caller_membership = await memberships.get(workspace_id, user_id)
    if caller_membership is None:
        return None
    return build_workspace_scope(
        conn,
        caller_membership,
        clock=clock,
        ids=ids,
        object_storage=object_storage,
        env=env,
    )


async def resolve_caller_membership(
    conn: asyncpg.Connection, workspace_id: UUID, user_id: UUID
) -> Membership | None:
    """A brief, standalone membership lookup — used by chat routes
    instead of ``resolve_workspace_scope``/``build_workspace_scope``,
    because those build a full scope bound to one connection meant to be
    held for the request's lifetime, which chat routes must never do
    (see ports.chat.MessageStorePort's docstring). The caller acquires
    and releases its own short-lived connection around this call."""
    return await PostgresMembershipRepository(conn).get(workspace_id, user_id)


def build_create_workspace_use_case(
    conn: asyncpg.Connection,
    *,
    clock: ClockPort,
    ids: IdPort,
    default_monthly_budget_microcents: int,
    default_budget_soft_pct: int,
) -> CreateWorkspace:
    """CreateWorkspace is the one workspace-mutation with no existing
    tenant to scope a connection to beforehand — see
    http/deps.py's get_new_workspace_connection."""
    workspaces = PostgresWorkspaceRepository(conn)
    memberships = PostgresMembershipRepository(conn)
    budgets = PostgresBudgetRepository(conn)
    audit_log = PostgresAuditLog(conn)
    return CreateWorkspace(
        workspaces=workspaces,
        memberships=memberships,
        budgets=budgets,
        audit_log=audit_log,
        clock=clock,
        ids=ids,
        default_monthly_budget_microcents=default_monthly_budget_microcents,
        default_budget_soft_pct=default_budget_soft_pct,
    )


def build_accept_invitation_use_case(
    conn: asyncpg.Connection, *, clock: ClockPort, ids: IdPort
) -> AcceptInvitation:
    """Built on a connection already scoped to the invitation's discovered
    workspace_id — see http/deps.py's get_invitation_acceptance_scope."""
    invitations = PostgresInvitationRepository(conn)
    memberships = PostgresMembershipRepository(conn)
    audit_log = PostgresAuditLog(conn)
    return AcceptInvitation(
        invitations=invitations, memberships=memberships, audit_log=audit_log, clock=clock, ids=ids
    )


async def build_container(settings: Settings) -> Container:
    db_pool = await create_pool(settings.database_url)
    redis_client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]  # redis-py gap, not ours
        settings.redis_url, decode_responses=True
    )

    users = PostgresUserRepository(db_pool)
    refresh_tokens = PostgresRefreshTokenRepository(db_pool)
    invitations = PostgresInvitationRepository(db_pool)
    api_keys = PostgresApiKeyRepository(db_pool)
    password_reset_tokens = PostgresPasswordResetTokenRepository(db_pool)
    audit_log = PostgresAuditLog(db_pool)
    outbox = PostgresOutboxRepository(db_pool)
    hasher = Argon2PasswordHasher()
    tokens = EdDSATokenSigner(
        signing_key_b64=settings.jwt_signing_key,
        kid=settings.jwt_kid,
        access_ttl_seconds=settings.jwt_access_ttl_seconds,
        previous_signing_key_b64=settings.jwt_previous_signing_key,
        previous_kid=settings.jwt_previous_kid,
    )
    clock = SystemClock()
    ids = Uuid7Generator()
    verify_api_key = VerifyApiKey(api_keys=api_keys, clock=clock)
    idempotency_store = RedisIdempotencyStore(redis_client)
    revocations = RedisJtiDenylist(redis_client)
    rate_limiter = FailOpenRateLimiter(
        RedisTokenBucketRateLimiter(redis_client, clock=clock),
        LocalTokenBucketRateLimiter(clock=clock),
    )

    revoke_user_sessions = RevokeUserSessions(refresh_tokens=refresh_tokens, clock=clock)

    message_store = PostgresMessageStore(db_pool)
    generator = _build_generator(settings, clock=clock)
    stream_buffer = RedisStreamBuffer(redis_client)
    cancellation = RedisCancellationChannel(redis_client)
    budget_admission = PostgresBudgetAdmission(
        db_pool, global_monthly_budget_microcents=settings.global_monthly_budget_microcents
    )
    usage_ledger = PostgresUsageLedger(db_pool)
    # Deliberately does NOT call ensure_bucket() here: bucket
    # provisioning is a one-time, out-of-band operational step (`make
    # minio-setup`), matching how migrations run as a separate step
    # from app boot rather than inside build_container() — every test
    # that constructs a Container via create_app()/TestClient goes
    # through this same function, and most of them have no reason to
    # need a running MinIO at all.
    object_storage = MinioObjectStorage(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        secure=settings.object_storage_secure,
        bucket=settings.object_storage_bucket,
    )
    embedder = _build_embedder(settings)
    query_rewriter = _build_query_rewriter(settings)
    chat_hybrid_search = HybridSearch(chunk_search=PooledChunkSearch(db_pool), embedder=embedder)
    chat_query_rewriter = QueryRewriter(rewriter=query_rewriter)
    chat_retrieval_gate = RetrievalGate(threshold=settings.retrieval_refusal_threshold)
    memory_compactor, memory_compactor_model = _build_memory_compactor(settings)
    chat_memory = MemoryAssembler(
        messages=message_store,
        summaries=PostgresMemorySummaryStore(db_pool),
        compactor=memory_compactor,
        compactor_model_label=memory_compactor_model,
        ids=ids,
    )

    return Container(
        db_pool=db_pool,
        redis_client=redis_client,
        env=settings.env,
        users=users,
        refresh_tokens=refresh_tokens,
        invitations=invitations,
        api_keys=api_keys,
        verify_api_key=verify_api_key,
        idempotency_store=idempotency_store,
        password_reset_tokens=password_reset_tokens,
        hasher=hasher,
        tokens=tokens,
        clock=clock,
        ids=ids,
        revocations=revocations,
        rate_limiter=rate_limiter,
        audit_log=audit_log,
        outbox=outbox,
        message_store=message_store,
        generator=generator,
        stream_buffer=stream_buffer,
        cancellation=cancellation,
        budget_admission=budget_admission,
        usage_ledger=usage_ledger,
        object_storage=object_storage,
        embedder=embedder,
        query_rewriter=query_rewriter,
        register_user=RegisterUser(users=users, hasher=hasher, audit_log=audit_log, ids=ids),
        login_user=LoginUser(
            users=users,
            refresh_tokens=refresh_tokens,
            hasher=hasher,
            tokens=tokens,
            clock=clock,
            ids=ids,
            audit_log=audit_log,
            refresh_ttl_seconds=settings.jwt_refresh_ttl_seconds,
        ),
        refresh_session=RefreshSession(
            refresh_tokens=refresh_tokens,
            tokens=tokens,
            clock=clock,
            ids=ids,
            refresh_ttl_seconds=settings.jwt_refresh_ttl_seconds,
            grace_seconds=settings.jwt_refresh_grace_seconds,
        ),
        logout_user=LogoutUser(
            refresh_tokens=refresh_tokens,
            revocations=revocations,
            clock=clock,
            audit_log=audit_log,
            ids=ids,
        ),
        revoke_user_sessions=revoke_user_sessions,
        request_password_reset=RequestPasswordReset(
            users=users,
            password_reset_tokens=password_reset_tokens,
            outbox=outbox,
            clock=clock,
            ids=ids,
        ),
        reset_password=ResetPassword(
            users=users,
            password_reset_tokens=password_reset_tokens,
            hasher=hasher,
            revoke_user_sessions=revoke_user_sessions,
            audit_log=audit_log,
            clock=clock,
            ids=ids,
        ),
        send_message=SendMessage(
            messages=message_store,
            generator=generator,
            hybrid_search=chat_hybrid_search,
            query_rewriter=chat_query_rewriter,
            retrieval_gate=chat_retrieval_gate,
            memory=chat_memory,
            buffer=stream_buffer,
            cancellation=cancellation,
            admission=budget_admission,
            usage_ledger=usage_ledger,
            ids=ids,
            max_tokens=settings.router_max_tokens,
            ceiling_cost_per_1k_microcents=settings.admission_ceiling_cost_per_1k_microcents,
        ),
        cancel_generation=CancelGeneration(cancellation=cancellation),
        get_generation_status=GetGenerationStatus(buffer=stream_buffer),
        get_usage=GetUsage(usage_ledger=usage_ledger),
        refresh_ttl_seconds=settings.jwt_refresh_ttl_seconds,
        default_workspace_monthly_budget_microcents=settings.default_workspace_monthly_budget_microcents,
        default_budget_soft_pct=settings.default_budget_soft_pct,
    )


def _build_generator(settings: Settings, *, clock: ClockPort) -> GeneratorPort:
    """Real providers only if configured (§3.2.4, issue #38) — dev/CI
    environments without SOPS-decrypted API keys fall back to
    EchoGenerator, exactly as S3 shipped. This is a real, honest
    fallback, not a silent stub: the meta event's ``model`` field always
    reflects which generator actually answered (see GeneratorPort.primary_model).

    Provider order below is also the fallback-chain order (LlmRouter
    tries providers in ``model_chain`` order, skipping any with an open
    circuit breaker) — OpenAI, then Anthropic, then Groq. Groq joins the
    chain only when ``groq_api_key`` is actually configured, the same
    "empty string is not a usable key" gate the other two providers
    already use; a missing/empty key never instantiates a live
    GroqCompletionAdapter, and Groq being unconfigured changes nothing
    about how OpenAI/Anthropic (or the EchoGenerator fallback) behave.
    """
    provider_configs: list[tuple[str, str, ProviderAdapterPort]] = []
    if settings.openai_api_key:
        provider_configs.append(
            ("openai", "gpt-4o-mini", OpenAiCompletionAdapter(api_key=settings.openai_api_key))
        )
    if settings.anthropic_api_key:
        provider_configs.append(
            (
                "anthropic",
                "claude-haiku-4-5",
                AnthropicCompletionAdapter(api_key=settings.anthropic_api_key),
            )
        )
    if settings.groq_api_key:
        provider_configs.append(
            (
                "groq",
                settings.groq_model,
                GroqCompletionAdapter(
                    api_key=settings.groq_api_key,
                    model=settings.groq_model,
                    base_url=settings.groq_base_url,
                ),
            )
        )
    if not provider_configs:
        return EchoGenerator()

    providers: dict[str, ProviderAdapterPort] = {
        name: adapter for name, _, adapter in provider_configs
    }
    breakers = {name: CircuitBreaker(clock=clock) for name, _, _ in provider_configs}
    model_chain = [(name, model) for name, model, _ in provider_configs]
    return LlmRouter(
        providers=providers,
        breakers=breakers,
        model_chain=model_chain,
        max_tokens=settings.router_max_tokens,
        max_concurrent_per_provider=settings.router_max_concurrent_per_provider,
    )


def _build_embedder(settings: Settings) -> EmbeddingProviderPort:
    """Real OpenAI embeddings only if configured (mirrors workers/
    composition.py's own ``_build_embedder``, issue #47) — dev/CI
    environments without a SOPS-decrypted API key fall back to
    LocalHashEmbeddingAdapter, a real and honest (if non-semantic)
    embedder, not a silent stub. The API process needs its own instance
    (query-time embedding for hybrid retrieval, issue #56) distinct
    from the worker's (document-time embedding, issue #47)."""
    if settings.openai_api_key:
        return OpenAiEmbeddingAdapter(api_key=settings.openai_api_key)
    return LocalHashEmbeddingAdapter()


def _build_query_rewriter(settings: Settings) -> QueryRewritePort:
    """Real cheap-model rewrite only if a provider key is configured
    (issue #57, mirrors _build_embedder/_build_generator's pattern) —
    a plain ProviderAdapterPort call, not routed through LlmRouter's
    fallback-chain/circuit-breaker machinery: a failed rewrite already
    falls back to the raw query one layer up (app/retrieval/
    query_rewrite.py), so retrying here would just spend the 150ms
    budget on something the caller is about to give up on anyway."""
    if settings.openai_api_key:
        return LlmQueryRewriteAdapter(
            provider=OpenAiCompletionAdapter(api_key=settings.openai_api_key), model="gpt-4o-mini"
        )
    if settings.anthropic_api_key:
        return LlmQueryRewriteAdapter(
            provider=AnthropicCompletionAdapter(api_key=settings.anthropic_api_key),
            model="claude-haiku-4-5",
        )
    return NoOpQueryRewriteAdapter()


def _build_memory_compactor(settings: Settings) -> tuple[MemoryCompactionPort, str]:
    """Real cheap-model compaction only if a provider key is configured
    (issue #82, same honest-fallback posture as _build_query_rewriter) —
    returns the port alongside the model label MemoryAssembler stamps
    onto every MemorySummary row it writes, so a reader always knows
    which compactor actually produced a given summary."""
    if settings.openai_api_key:
        return (
            LlmMemoryCompactionAdapter(
                provider=OpenAiCompletionAdapter(api_key=settings.openai_api_key),
                model="gpt-4o-mini",
            ),
            "gpt-4o-mini",
        )
    if settings.anthropic_api_key:
        return (
            LlmMemoryCompactionAdapter(
                provider=AnthropicCompletionAdapter(api_key=settings.anthropic_api_key),
                model="claude-haiku-4-5",
            ),
            "claude-haiku-4-5",
        )
    return TruncatingMemoryCompactionAdapter(), TRUNCATING_MEMORY_COMPACTION_MODEL_NAME

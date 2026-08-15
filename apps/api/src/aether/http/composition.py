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

from aether.adapters.argon2.hasher import Argon2PasswordHasher
from aether.adapters.clock import SystemClock
from aether.adapters.idgen import Uuid7Generator
from aether.adapters.jwt.eddsa import EdDSATokenSigner
from aether.adapters.postgres.audit_log import PostgresAuditLog
from aether.adapters.postgres.invitation_repository import PostgresInvitationRepository
from aether.adapters.postgres.membership_repository import PostgresMembershipRepository
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.password_reset_token_repository import (
    PostgresPasswordResetTokenRepository,
)
from aether.adapters.postgres.pool import create_pool
from aether.adapters.postgres.refresh_token_repository import PostgresRefreshTokenRepository
from aether.adapters.postgres.user_repository import PostgresUserRepository
from aether.adapters.postgres.workspace_repository import PostgresWorkspaceRepository
from aether.adapters.redis.denylist import RedisJtiDenylist
from aether.adapters.redis.rate_limiter import (
    FailOpenRateLimiter,
    LocalTokenBucketRateLimiter,
    RedisTokenBucketRateLimiter,
)
from aether.app.auth.login_user import LoginUser
from aether.app.auth.logout_user import LogoutUser
from aether.app.auth.refresh_session import RefreshSession
from aether.app.auth.register_user import RegisterUser
from aether.app.auth.revoke_user_sessions import RevokeUserSessions
from aether.app.invitations.accept_invitation import AcceptInvitation
from aether.app.invitations.create_invitation import CreateInvitation
from aether.app.invitations.revoke_invitation import RevokeInvitation
from aether.app.password_reset.request_password_reset import RequestPasswordReset
from aether.app.password_reset.reset_password import ResetPassword
from aether.app.workspaces.create_workspace import CreateWorkspace
from aether.app.workspaces.delete_workspace import DeleteWorkspace
from aether.app.workspaces.get_workspace import GetWorkspace
from aether.app.workspaces.manage_members import ListMembers, RemoveMember, UpdateMemberRole
from aether.app.workspaces.update_workspace import UpdateWorkspace
from aether.config import Settings
from aether.ports.audit import AuditLogPort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.rate_limit import RateLimitPort
from aether.ports.repositories import (
    InvitationRepositoryPort,
    Membership,
    MembershipRepositoryPort,
    PasswordResetTokenRepositoryPort,
    RefreshTokenRepositoryPort,
    UserRepositoryPort,
    WorkspaceRepositoryPort,
)
from aether.ports.revocation import RevocationPort
from aether.ports.security import ClockPort, IdPort, PasswordHasherPort, TokenPort


@dataclass
class Container:
    db_pool: asyncpg.Pool
    redis_client: redis_asyncio.Redis

    users: UserRepositoryPort
    refresh_tokens: RefreshTokenRepositoryPort
    invitations: InvitationRepositoryPort
    """Pool-bound, not connection-scoped: invitations is RLS-exempt (see
    its migration), so unlike workspaces/memberships it needs no
    per-request tenant context — this is the instance used for the
    accept-by-token lookup, which by definition runs before any tenant
    scope is known. See http/deps.py's get_invitation_acceptance_scope."""
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

    register_user: RegisterUser
    login_user: LoginUser
    refresh_session: RefreshSession
    logout_user: LogoutUser
    revoke_user_sessions: RevokeUserSessions
    request_password_reset: RequestPasswordReset
    reset_password: ResetPassword

    refresh_ttl_seconds: int

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
    memberships: MembershipRepositoryPort
    invitations: InvitationRepositoryPort
    audit_log: AuditLogPort
    outbox: OutboxRepositoryPort

    get_workspace: GetWorkspace
    update_workspace: UpdateWorkspace
    delete_workspace: DeleteWorkspace
    list_members: ListMembers
    update_member_role: UpdateMemberRole
    remove_member: RemoveMember
    create_invitation: CreateInvitation
    revoke_invitation: RevokeInvitation


def build_workspace_scope(
    conn: asyncpg.Connection,
    caller_membership: Membership,
    *,
    clock: ClockPort,
    ids: IdPort,
) -> WorkspaceScope:
    workspaces = PostgresWorkspaceRepository(conn)
    memberships = PostgresMembershipRepository(conn)
    invitations = PostgresInvitationRepository(conn)
    audit_log = PostgresAuditLog(conn)
    outbox = PostgresOutboxRepository(conn)
    return WorkspaceScope(
        conn=conn,
        caller_membership=caller_membership,
        workspaces=workspaces,
        memberships=memberships,
        invitations=invitations,
        audit_log=audit_log,
        outbox=outbox,
        get_workspace=GetWorkspace(workspaces=workspaces),
        update_workspace=UpdateWorkspace(workspaces=workspaces, audit_log=audit_log, ids=ids),
        delete_workspace=DeleteWorkspace(
            workspaces=workspaces, audit_log=audit_log, clock=clock, ids=ids
        ),
        list_members=ListMembers(memberships=memberships),
        update_member_role=UpdateMemberRole(memberships=memberships, audit_log=audit_log, ids=ids),
        remove_member=RemoveMember(memberships=memberships, audit_log=audit_log, ids=ids),
        create_invitation=CreateInvitation(
            invitations=invitations, audit_log=audit_log, outbox=outbox, clock=clock, ids=ids
        ),
        revoke_invitation=RevokeInvitation(invitations=invitations, audit_log=audit_log, ids=ids),
    )


async def resolve_workspace_scope(
    conn: asyncpg.Connection,
    workspace_id: UUID,
    user_id: UUID,
    *,
    clock: ClockPort,
    ids: IdPort,
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
    return build_workspace_scope(conn, caller_membership, clock=clock, ids=ids)


def build_create_workspace_use_case(conn: asyncpg.Connection, *, ids: IdPort) -> CreateWorkspace:
    """CreateWorkspace is the one workspace-mutation with no existing
    tenant to scope a connection to beforehand — see
    http/deps.py's get_new_workspace_connection."""
    workspaces = PostgresWorkspaceRepository(conn)
    memberships = PostgresMembershipRepository(conn)
    audit_log = PostgresAuditLog(conn)
    return CreateWorkspace(
        workspaces=workspaces, memberships=memberships, audit_log=audit_log, ids=ids
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
    password_reset_tokens = PostgresPasswordResetTokenRepository(db_pool)
    audit_log = PostgresAuditLog(db_pool)
    outbox = PostgresOutboxRepository(db_pool)
    hasher = Argon2PasswordHasher()
    tokens = EdDSATokenSigner(
        signing_key_b64=settings.jwt_signing_key,
        kid=settings.jwt_kid,
        access_ttl_seconds=settings.jwt_access_ttl_seconds,
    )
    clock = SystemClock()
    ids = Uuid7Generator()
    revocations = RedisJtiDenylist(redis_client)
    rate_limiter = FailOpenRateLimiter(
        RedisTokenBucketRateLimiter(redis_client, clock=clock),
        LocalTokenBucketRateLimiter(clock=clock),
    )

    revoke_user_sessions = RevokeUserSessions(refresh_tokens=refresh_tokens, clock=clock)

    return Container(
        db_pool=db_pool,
        redis_client=redis_client,
        users=users,
        refresh_tokens=refresh_tokens,
        invitations=invitations,
        password_reset_tokens=password_reset_tokens,
        hasher=hasher,
        tokens=tokens,
        clock=clock,
        ids=ids,
        revocations=revocations,
        rate_limiter=rate_limiter,
        audit_log=audit_log,
        outbox=outbox,
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
        refresh_ttl_seconds=settings.jwt_refresh_ttl_seconds,
    )

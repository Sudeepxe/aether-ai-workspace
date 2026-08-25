"""FastAPI dependency providers — the only place ``request.app.state`` is
touched, and the only place a Bearer header is turned into a verified
session (Blueprint ADR-7.1/7.2)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from aether.app.api_keys.verify_api_key import ApiKeyPrincipal
from aether.app.auth.api_keys import is_api_key
from aether.app.auth.tokens import hash_token
from aether.app.invitations.accept_invitation import AcceptInvitation
from aether.app.workspaces.create_workspace import CreateWorkspace
from aether.domain.entities import ApiKeyScope, MembershipRole
from aether.domain.errors import InvalidAccessTokenError, WorkspaceNotFoundError
from aether.domain.policy import Capability, role_may
from aether.http.composition import (
    Container,
    WorkspaceScope,
    build_accept_invitation_use_case,
    build_create_workspace_use_case,
    resolve_caller_membership,
    resolve_workspace_scope,
)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user_id: UUID
    jti: UUID
    expires_at: datetime


async def get_current_session(
    container: Container = Depends(get_container),
    authorization: str | None = Header(default=None),
) -> AuthenticatedSession:
    if authorization is None or not authorization.startswith("Bearer "):
        raise InvalidAccessTokenError("missing bearer token")
    token = authorization.removeprefix("Bearer ")

    claims = container.tokens.verify_access_token(token)  # raises InvalidAccessTokenError

    if await container.revocations.is_denied(claims.jti):
        raise InvalidAccessTokenError("token revoked")

    return AuthenticatedSession(user_id=claims.sub, jti=claims.jti, expires_at=claims.expires_at)


async def get_session_or_api_key(
    container: Container = Depends(get_container),
    authorization: str | None = Header(default=None),
) -> AuthenticatedSession | ApiKeyPrincipal:
    """The API-key-eligible counterpart to ``get_current_session`` — for
    routes marked ``S,K`` in §4.3's resource catalog, which accept either
    a human JWT session or a machine API key on the same Authorization
    header. ``is_api_key`` distinguishes the two unambiguously (a JWT
    never starts with ``aeth_``), so this never guesses."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise InvalidAccessTokenError("missing bearer token")
    token = authorization.removeprefix("Bearer ")

    if is_api_key(token):
        return await container.verify_api_key.execute(token)  # raises InvalidApiKeyError

    claims = container.tokens.verify_access_token(token)  # raises InvalidAccessTokenError
    if await container.revocations.is_denied(claims.jti):
        raise InvalidAccessTokenError("token revoked")
    return AuthenticatedSession(user_id=claims.sub, jti=claims.jti, expires_at=claims.expires_at)


@dataclass(frozen=True, slots=True)
class ChatPrincipal:
    """Unifies the two authorization primitives §4.3's ``S,K`` routes
    must accept: a human session's MembershipRole (checked via
    domain.policy's role-capability table) or an API key's explicit
    scope grant (§7.4 — a coarser, independent model; an API key isn't
    "a Member", it's "allowed to do X"). Exactly one of the two fields
    is set."""

    role: MembershipRole | None
    api_key_scopes: frozenset[ApiKeyScope] | None

    def has_scope(self, capability: Capability, scope: ApiKeyScope) -> bool:
        if self.api_key_scopes is not None:
            return scope in self.api_key_scopes
        assert self.role is not None  # noqa: S101 — exactly one field is always set
        return role_may(self.role, capability)


def require_chat_scope(
    principal: ChatPrincipal, capability: Capability, scope: ApiKeyScope
) -> None:
    """The ChatPrincipal counterpart to http/authz.py's
    ``require_capability`` — same 403-not-404 reasoning (§3.6.1: by the
    time a route calls this, get_chat_authorization has already 404'd a
    caller with no access to this workspace at all)."""
    if not principal.has_scope(capability, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope")


def device_fingerprint(user_agent: str | None = Header(default=None)) -> str:
    """A coarse, real (not fabricated) device signal: the User-Agent header,
    hashed to a fixed-length value for storage. ADR-7.2 calls for *a*
    per-device fingerprint without mandating its derivation; nothing in
    Sprint 1 needs finer-grained device identification than this."""
    raw = user_agent or "unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_workspace_scope(
    workspace_id: UUID,
    session: AuthenticatedSession = Depends(get_current_session),
    container: Container = Depends(get_container),
) -> AsyncIterator[WorkspaceScope]:
    """One connection, one transaction, for the whole request:
    ``app.tenant_id`` is set once here (§3.7.2 layer 4) and every
    repository call the route makes shares it, so a business mutation and
    its audit-log write either both commit or both roll back together.
    A missing membership raises WorkspaceNotFoundError — the same 404 a
    genuinely nonexistent workspace produces, so a cross-tenant probe
    can't distinguish "wrong id" from "not your workspace" (§3.7.1)."""
    async with container.db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        scope = await resolve_workspace_scope(
            conn,
            workspace_id,
            session.user_id,
            clock=container.clock,
            ids=container.ids,
            object_storage=container.object_storage,
            env=container.env,
        )
        if scope is None:
            raise WorkspaceNotFoundError(str(workspace_id))
        yield scope


async def get_new_workspace_connection(
    session: AuthenticatedSession = Depends(get_current_session),
    container: Container = Depends(get_container),
) -> AsyncIterator[tuple[UUID, CreateWorkspace]]:
    """POST /workspaces has no existing tenant to scope to — the id is
    generated here, tenant context is set to *that* id immediately, and
    the same id is threaded into CreateWorkspaceCommand so the owner
    membership INSERT's WITH CHECK matches (see CreateWorkspace's own
    docstring for why this can't just happen inside the use case)."""
    workspace_id = container.ids.new_id()
    async with container.db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        yield (
            workspace_id,
            build_create_workspace_use_case(
                conn,
                clock=container.clock,
                ids=container.ids,
                default_monthly_budget_microcents=container.default_workspace_monthly_budget_microcents,
                default_budget_soft_pct=container.default_budget_soft_pct,
            ),
        )


async def get_chat_authorization(
    workspace_id: UUID,
    session_or_key: AuthenticatedSession | ApiKeyPrincipal = Depends(get_session_or_api_key),
    container: Container = Depends(get_container),
) -> ChatPrincipal:
    """Unlike ``get_workspace_scope``, this briefly acquires a connection
    to resolve the caller's membership and releases it immediately —
    chat routes must never hold a connection open for a token stream's
    duration (see ports.chat.MessageStorePort's docstring). Persistence
    for the actual chat turn goes through ``container.message_store``
    instead, which manages its own short-lived connections per call.

    An API-key principal never needs a membership lookup at all — it's
    already workspace-scoped by construction (the key belongs to exactly
    one workspace); a key presented against a *different* workspace's
    URL raises the same WorkspaceNotFoundError a human's missing
    membership would (§3.7.1: no existence oracle either way)."""
    if isinstance(session_or_key, ApiKeyPrincipal):
        if session_or_key.workspace_id != workspace_id:
            raise WorkspaceNotFoundError(str(workspace_id))
        return ChatPrincipal(role=None, api_key_scopes=session_or_key.scopes)

    async with container.db_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
        membership = await resolve_caller_membership(conn, workspace_id, session_or_key.user_id)
    if membership is None:
        raise WorkspaceNotFoundError(str(workspace_id))
    return ChatPrincipal(role=membership.role, api_key_scopes=None)


async def get_invitation_acceptance_scope(
    token: str,
    container: Container = Depends(get_container),
) -> AsyncIterator[AcceptInvitation]:
    """The accepting user isn't a member of anything yet, so there's no
    URL-path workspace_id to scope from. An initial pool-bound lookup
    (invitations has no RLS — see its migration) discovers the tenant;
    AcceptInvitation.execute() does its own full validation regardless of
    what this finds, so an unknown/expired/consumed token still reaches
    the same InvalidInvitationError either way, just via a connection
    with no tenant context set (harmless: nothing this use case touches
    needs one when there's no real invitation to act on)."""
    lookup = await container.invitations.get_by_token_hash(hash_token(token))
    async with container.db_pool.acquire() as conn, conn.transaction():
        if lookup is not None:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(lookup.workspace_id)
            )
        yield build_accept_invitation_use_case(conn, clock=container.clock, ids=container.ids)

"""Postgres-backed InvitationRepositoryPort implementation.

Unlike workspace/membership/audit_log, this one accepts either a bare
Pool or a Connection: invitations is RLS-exempt (no per-request tenant
context to preserve across calls), so the accept-by-token lookup can run
directly against the pool-bound singleton instance in Container, while
admin-facing create/revoke run against the request's already-open
tenant-scoped connection for transactional consistency with the
membership-role check and audit log write. asyncpg.Pool and
asyncpg.Connection both expose the same fetchrow/fetch/execute surface
this adapter uses, so either works structurally.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from aether.ports.repositories import Invitation, MembershipRole


class PostgresInvitationRepository:
    def __init__(self, conn: asyncpg.Pool | asyncpg.Connection) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        email: str,
        role: MembershipRole,
        token_hash: str,
        invited_by: UUID,
        expires_at: datetime,
    ) -> Invitation:
        row = await self._conn.fetchrow(
            """
            INSERT INTO invitations
                (id, workspace_id, email, role, token_hash, invited_by, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, workspace_id, email, role, token_hash, invited_by,
                      expires_at, consumed_at, created_at
            """,
            id,
            workspace_id,
            email,
            role.value,
            token_hash,
            invited_by,
            expires_at,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_invitation(row)

    async def get_by_token_hash(self, token_hash: str) -> Invitation | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, email, role, token_hash, invited_by, "
            "expires_at, consumed_at, created_at FROM invitations WHERE token_hash = $1",
            token_hash,
        )
        return _row_to_invitation(row) if row is not None else None

    async def consume(self, invitation_id: UUID, *, consumed_at: datetime) -> None:
        await self._conn.execute(
            "UPDATE invitations SET consumed_at = $2 WHERE id = $1", invitation_id, consumed_at
        )

    async def delete(self, workspace_id: UUID, invitation_id: UUID) -> None:
        await self._conn.execute(
            "DELETE FROM invitations WHERE workspace_id = $1 AND id = $2",
            workspace_id,
            invitation_id,
        )


def _row_to_invitation(row: asyncpg.Record) -> Invitation:
    return Invitation(
        id=row["id"],
        workspace_id=row["workspace_id"],
        email=row["email"],
        role=MembershipRole(row["role"]),
        token_hash=row["token_hash"],
        invited_by=row["invited_by"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
        created_at=row["created_at"],
    )

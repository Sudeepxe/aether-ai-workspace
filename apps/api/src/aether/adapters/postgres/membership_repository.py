"""Postgres-backed MembershipRepositoryPort implementation.

Connection-bound (see workspace_repository.py's docstring for why) — every
call in a request shares the one transaction with app.tenant_id already
set.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.repositories import Membership, MembershipRole


class PostgresMembershipRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(
        self, *, id: UUID, workspace_id: UUID, user_id: UUID, role: MembershipRole
    ) -> Membership:
        row = await self._conn.fetchrow(
            """
            INSERT INTO memberships (id, workspace_id, user_id, role)
            VALUES ($1, $2, $3, $4)
            RETURNING id, workspace_id, user_id, role, created_at, updated_at
            """,
            id,
            workspace_id,
            user_id,
            role.value,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_membership(row)

    async def get(self, workspace_id: UUID, user_id: UUID) -> Membership | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, user_id, role, created_at, updated_at "
            "FROM memberships WHERE workspace_id = $1 AND user_id = $2",
            workspace_id,
            user_id,
        )
        return _row_to_membership(row) if row is not None else None

    async def list_by_workspace(self, workspace_id: UUID) -> list[Membership]:
        rows = await self._conn.fetch(
            "SELECT id, workspace_id, user_id, role, created_at, updated_at "
            "FROM memberships WHERE workspace_id = $1 ORDER BY created_at",
            workspace_id,
        )
        return [_row_to_membership(row) for row in rows]

    async def update_role(
        self, workspace_id: UUID, user_id: UUID, *, role: MembershipRole
    ) -> Membership | None:
        row = await self._conn.fetchrow(
            """
            UPDATE memberships SET role = $3, updated_at = now()
            WHERE workspace_id = $1 AND user_id = $2
            RETURNING id, workspace_id, user_id, role, created_at, updated_at
            """,
            workspace_id,
            user_id,
            role.value,
        )
        return _row_to_membership(row) if row is not None else None

    async def delete(self, workspace_id: UUID, user_id: UUID) -> None:
        await self._conn.execute(
            "DELETE FROM memberships WHERE workspace_id = $1 AND user_id = $2",
            workspace_id,
            user_id,
        )

    async def count_by_role(self, workspace_id: UUID, role: MembershipRole) -> int:
        count = await self._conn.fetchval(
            "SELECT count(*) FROM memberships WHERE workspace_id = $1 AND role = $2",
            workspace_id,
            role.value,
        )
        return int(count)


def _row_to_membership(row: asyncpg.Record) -> Membership:
    return Membership(
        id=row["id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        role=MembershipRole(row["role"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

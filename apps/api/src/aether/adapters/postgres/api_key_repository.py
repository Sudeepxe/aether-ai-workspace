"""Postgres-backed ApiKeyRepositoryPort implementation.

Accepts either a bare Pool or a Connection: api_keys is RLS-exempt (see
its migration) for the identical invitations reason — the verification
read path (``get_by_prefix``) runs before any tenant context exists, so
it's used against the pool-bound singleton in Container; admin-facing
create/list/revoke run against the request's already-open tenant-scoped
connection for transactional consistency with the audit-log write.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from aether.ports.repositories import ApiKey, ApiKeyScope


class PostgresApiKeyRepository:
    def __init__(self, conn: asyncpg.Pool | asyncpg.Connection) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        prefix: str,
        secret_hash: str,
        name: str,
        scopes: frozenset[ApiKeyScope],
        created_by: UUID,
        expires_at: datetime | None,
    ) -> ApiKey:
        row = await self._conn.fetchrow(
            """
            INSERT INTO api_keys
                (id, workspace_id, prefix, secret_hash, name, scopes, created_by, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, workspace_id, prefix, secret_hash, name, scopes, created_by,
                      expires_at, revoked_at, last_used_at, created_at
            """,
            id,
            workspace_id,
            prefix,
            secret_hash,
            name,
            [s.value for s in scopes],
            created_by,
            expires_at,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_api_key(row)

    async def get_by_prefix(self, prefix: str) -> ApiKey | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, prefix, secret_hash, name, scopes, created_by, "
            "expires_at, revoked_at, last_used_at, created_at FROM api_keys WHERE prefix = $1",
            prefix,
        )
        return _row_to_api_key(row) if row is not None else None

    async def list_by_workspace(self, workspace_id: UUID) -> list[ApiKey]:
        rows = await self._conn.fetch(
            "SELECT id, workspace_id, prefix, secret_hash, name, scopes, created_by, "
            "expires_at, revoked_at, last_used_at, created_at FROM api_keys "
            "WHERE workspace_id = $1 ORDER BY created_at DESC",
            workspace_id,
        )
        return [_row_to_api_key(row) for row in rows]

    async def revoke(self, workspace_id: UUID, key_id: UUID, *, revoked_at: datetime) -> None:
        await self._conn.execute(
            "UPDATE api_keys SET revoked_at = $3 WHERE workspace_id = $1 AND id = $2",
            workspace_id,
            key_id,
            revoked_at,
        )

    async def touch_last_used(self, key_id: UUID, *, used_at: datetime) -> None:
        # The WHERE clause is what actually coarsens this to hourly
        # granularity (§7.8 F-4): once a key has been used this hour,
        # every subsequent request's touch matches zero rows and is a
        # no-op UPDATE — real hourly buffering without a separate queue
        # or background flush mechanism.
        await self._conn.execute(
            "UPDATE api_keys SET last_used_at = $2::timestamptz WHERE id = $1 "
            "AND (last_used_at IS NULL OR last_used_at < $2::timestamptz - INTERVAL '1 hour')",
            key_id,
            used_at,
        )


def _row_to_api_key(row: asyncpg.Record) -> ApiKey:
    return ApiKey(
        id=row["id"],
        workspace_id=row["workspace_id"],
        prefix=row["prefix"],
        secret_hash=row["secret_hash"],
        name=row["name"],
        scopes=frozenset(ApiKeyScope(s) for s in row["scopes"]),
        created_by=row["created_by"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
        created_at=row["created_at"],
    )

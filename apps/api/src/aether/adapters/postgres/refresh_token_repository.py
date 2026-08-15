"""Postgres-backed RefreshTokenRepositoryPort implementation (ADR-7.2)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from aether.ports.repositories import RefreshToken


class PostgresRefreshTokenRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        id: UUID,
        user_id: UUID,
        family_id: UUID,
        token_hash: str,
        device_fingerprint: str,
        expires_at: datetime,
    ) -> RefreshToken:
        row = await self._pool.fetchrow(
            """
            INSERT INTO refresh_tokens
                (id, user_id, family_id, token_hash, device_fingerprint, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, user_id, family_id, token_hash, device_fingerprint,
                      used_at, successor_id, expires_at, revoked_at, created_at
            """,
            id,
            user_id,
            family_id,
            token_hash,
            device_fingerprint,
            expires_at,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_refresh_token(row)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, user_id, family_id, token_hash, device_fingerprint,
                   used_at, successor_id, expires_at, revoked_at, created_at
            FROM refresh_tokens WHERE token_hash = $1
            """,
            token_hash,
        )
        return _row_to_refresh_token(row) if row is not None else None

    async def mark_used(self, token_id: UUID, *, successor_id: UUID, used_at: datetime) -> None:
        await self._pool.execute(
            "UPDATE refresh_tokens SET used_at = $2, successor_id = $3 WHERE id = $1",
            token_id,
            used_at,
            successor_id,
        )

    async def revoke_family(self, family_id: UUID, *, revoked_at: datetime) -> None:
        await self._pool.execute(
            "UPDATE refresh_tokens SET revoked_at = $2 WHERE family_id = $1 AND revoked_at IS NULL",
            family_id,
            revoked_at,
        )

    async def revoke_all_for_user(self, user_id: UUID, *, revoked_at: datetime) -> None:
        await self._pool.execute(
            "UPDATE refresh_tokens SET revoked_at = $2 WHERE user_id = $1 AND revoked_at IS NULL",
            user_id,
            revoked_at,
        )


def _row_to_refresh_token(row: asyncpg.Record) -> RefreshToken:
    return RefreshToken(
        id=row["id"],
        user_id=row["user_id"],
        family_id=row["family_id"],
        token_hash=row["token_hash"],
        device_fingerprint=row["device_fingerprint"],
        used_at=row["used_at"],
        successor_id=row["successor_id"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        created_at=row["created_at"],
    )

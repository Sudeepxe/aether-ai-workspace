"""Postgres-backed PasswordResetTokenRepositoryPort implementation
(ADR-11.1). Pool-bound like refresh_tokens/invitations — password-reset
tokens are RLS-exempt (looked up by hash before any session exists,
identical reasoning to those two)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from aether.ports.repositories import PasswordResetToken


class PostgresPasswordResetTokenRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self, *, id: UUID, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> PasswordResetToken:
        row = await self._pool.fetchrow(
            """
            INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id, user_id, token_hash, expires_at, consumed_at, created_at
            """,
            id,
            user_id,
            token_hash,
            expires_at,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_token(row)

    async def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        row = await self._pool.fetchrow(
            "SELECT id, user_id, token_hash, expires_at, consumed_at, created_at "
            "FROM password_reset_tokens WHERE token_hash = $1",
            token_hash,
        )
        return _row_to_token(row) if row is not None else None

    async def consume(self, token_id: UUID, *, consumed_at: datetime) -> None:
        await self._pool.execute(
            "UPDATE password_reset_tokens SET consumed_at = $2 WHERE id = $1",
            token_id,
            consumed_at,
        )


def _row_to_token(row: asyncpg.Record) -> PasswordResetToken:
    return PasswordResetToken(
        id=row["id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
        created_at=row["created_at"],
    )

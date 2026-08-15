"""Postgres-backed UserRepositoryPort implementation.

Translates asyncpg-specific errors (e.g. a unique-constraint violation on
email) into domain errors here, at the adapter boundary — app/ never
imports asyncpg, per the hexagon's import rule (ADR-3.4).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.repositories import EmailAlreadyRegisteredError, User


class PostgresUserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self, *, id: UUID, email: str, display_name: str, password_hash: str | None
    ) -> User:
        try:
            row = await self._pool.fetchrow(
                """
                INSERT INTO users (id, email, display_name, password_hash)
                VALUES ($1, $2, $3, $4)
                RETURNING id, email, display_name, password_hash, status, created_at, updated_at
                """,
                id,
                email,
                display_name,
                password_hash,
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise EmailAlreadyRegisteredError(email) from exc
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_user(row)

    async def get_by_id(self, user_id: UUID) -> User | None:
        row = await self._pool.fetchrow(
            "SELECT id, email, display_name, password_hash, status, created_at, updated_at "
            "FROM users WHERE id = $1",
            user_id,
        )
        return _row_to_user(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        # users.email is CITEXT — this comparison is case-insensitive at
        # the database level, not something the repository has to emulate.
        row = await self._pool.fetchrow(
            "SELECT id, email, display_name, password_hash, status, created_at, updated_at "
            "FROM users WHERE email = $1",
            email,
        )
        return _row_to_user(row) if row is not None else None

    async def update_password_hash(self, user_id: UUID, *, password_hash: str) -> None:
        await self._pool.execute(
            "UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1",
            user_id,
            password_hash,
        )


def _row_to_user(row: asyncpg.Record) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        password_hash=row["password_hash"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

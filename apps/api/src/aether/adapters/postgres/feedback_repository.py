"""Postgres-backed FeedbackRepositoryPort implementation.

Connection-bound (see citation_repository.py's docstring for the same
pattern) — issue #83 composes ``upsert`` into the request's own
WorkspaceScope transaction, unlike citations there's no at-least-once
redelivery concern to design around: a caller resubmitting feedback is
a genuine, intentional "change my mind" upsert, not a retry.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.repositories import Feedback, FeedbackRating


class PostgresFeedbackRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def upsert(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        message_id: UUID,
        user_id: UUID,
        rating: FeedbackRating,
        reason: str | None,
    ) -> Feedback:
        row = await self._conn.fetchrow(
            """
            INSERT INTO feedback (id, workspace_id, message_id, user_id, rating, reason)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (message_id, user_id) DO UPDATE SET
                rating = EXCLUDED.rating,
                reason = EXCLUDED.reason,
                updated_at = now()
            RETURNING id, workspace_id, message_id, user_id, rating, reason,
                      created_at, updated_at
            """,
            id,
            workspace_id,
            message_id,
            user_id,
            rating.value,
            reason,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row
        return _row_to_feedback(row)

    async def list_by_messages_for_user(
        self, workspace_id: UUID, message_ids: list[UUID], user_id: UUID
    ) -> list[Feedback]:
        if not message_ids:
            return []
        rows = await self._conn.fetch(
            "SELECT id, workspace_id, message_id, user_id, rating, reason, created_at, updated_at "
            "FROM feedback WHERE workspace_id = $1 AND message_id = ANY($2::uuid[]) "
            "AND user_id = $3",
            workspace_id,
            message_ids,
            user_id,
        )
        return [_row_to_feedback(row) for row in rows]


def _row_to_feedback(row: asyncpg.Record) -> Feedback:
    return Feedback(
        id=row["id"],
        workspace_id=row["workspace_id"],
        message_id=row["message_id"],
        user_id=row["user_id"],
        rating=FeedbackRating(row["rating"]),
        reason=row["reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

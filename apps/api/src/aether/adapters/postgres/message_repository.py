"""Postgres-backed MessageRepositoryPort implementation.

Connection-bound (see workspace_repository.py's docstring) — the fast,
non-streaming list/get routes use this directly against
``get_workspace_scope``'s one-connection-per-request. The streaming
orchestrator instead goes through ``ports.chat.MessageStorePort``
(adapters/postgres/message_store.py), which wraps this same ``create()``
in its own short-lived connection per call — see that module's docstring
for why.

``create()`` implements ADR-8.2's seq assignment: lock the thread's
counter row, read-increment-write it, then insert the message with that
value — all inside the caller's transaction, so seq assignment and the
message write commit or roll back together. Lock contention is scoped to
one thread's counter row, never cross-thread.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.repositories import Message, MessageRole, MessageStatus


class PostgresMessageRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        thread_id: UUID,
        role: MessageRole,
        content: str,
        status: MessageStatus,
        client_message_id: str | None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_microcents: int | None = None,
        grounded: bool = False,
    ) -> Message:
        # The counter row is created lazily on first message rather than
        # at thread-creation time, so a thread with zero messages costs
        # zero counter rows. `SELECT ... FOR UPDATE` only locks a row
        # that already exists, so it provides no protection by itself
        # against two concurrent *first* messages both finding no row —
        # `INSERT ... ON CONFLICT DO NOTHING` first (which does take a
        # real row lock, even on a no-op conflict, forcing the loser to
        # wait for the winner's transaction to finish) makes the
        # existence check itself race-free before the FOR UPDATE lock.
        await self._conn.execute(
            "INSERT INTO thread_seq_counters (thread_id, next_seq) VALUES ($1, 1) "
            "ON CONFLICT (thread_id) DO NOTHING",
            thread_id,
        )
        counter_row = await self._conn.fetchrow(
            "SELECT next_seq FROM thread_seq_counters WHERE thread_id = $1 FOR UPDATE",
            thread_id,
        )
        assert counter_row is not None  # noqa: S101 — the INSERT above guarantees the row exists
        seq = counter_row["next_seq"]
        await self._conn.execute(
            "UPDATE thread_seq_counters SET next_seq = $2 WHERE thread_id = $1",
            thread_id,
            seq + 1,
        )

        row = await self._conn.fetchrow(
            """
            INSERT INTO messages (
                id, workspace_id, thread_id, seq, role, content, status,
                client_message_id, model, prompt_tokens, completion_tokens,
                cost_microcents, grounded
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING id, workspace_id, thread_id, seq, role, content, status,
                      client_message_id, model, prompt_tokens, completion_tokens,
                      cost_microcents, grounded, created_at
            """,
            id,
            workspace_id,
            thread_id,
            seq,
            role.value,
            content,
            status.value,
            client_message_id,
            model,
            prompt_tokens,
            completion_tokens,
            cost_microcents,
            grounded,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_message(row)

    async def get_by_client_message_id(
        self, workspace_id: UUID, thread_id: UUID, client_message_id: str
    ) -> Message | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, thread_id, seq, role, content, status, client_message_id, "
            "model, prompt_tokens, completion_tokens, cost_microcents, grounded, created_at "
            "FROM messages WHERE workspace_id = $1 AND thread_id = $2 AND client_message_id = $3",
            workspace_id,
            thread_id,
            client_message_id,
        )
        return _row_to_message(row) if row is not None else None

    async def get_by_seq(self, workspace_id: UUID, thread_id: UUID, seq: int) -> Message | None:
        row = await self._conn.fetchrow(
            "SELECT id, workspace_id, thread_id, seq, role, content, status, client_message_id, "
            "model, prompt_tokens, completion_tokens, cost_microcents, grounded, created_at "
            "FROM messages WHERE workspace_id = $1 AND thread_id = $2 AND seq = $3",
            workspace_id,
            thread_id,
            seq,
        )
        return _row_to_message(row) if row is not None else None

    async def list_by_thread(
        self, workspace_id: UUID, thread_id: UUID, *, after_seq: int | None, limit: int
    ) -> list[Message]:
        if after_seq is None:
            rows = await self._conn.fetch(
                "SELECT id, workspace_id, thread_id, seq, role, content, status, client_message_id, "
                "model, prompt_tokens, completion_tokens, cost_microcents, grounded, created_at "
                "FROM messages WHERE workspace_id = $1 AND thread_id = $2 "
                "ORDER BY seq DESC LIMIT $3",
                workspace_id,
                thread_id,
                limit,
            )
        else:
            rows = await self._conn.fetch(
                "SELECT id, workspace_id, thread_id, seq, role, content, status, client_message_id, "
                "model, prompt_tokens, completion_tokens, cost_microcents, grounded, created_at "
                "FROM messages WHERE workspace_id = $1 AND thread_id = $2 AND seq < $3 "
                "ORDER BY seq DESC LIMIT $4",
                workspace_id,
                thread_id,
                after_seq,
                limit,
            )
        return [_row_to_message(row) for row in rows]


def _row_to_message(row: asyncpg.Record) -> Message:
    return Message(
        id=row["id"],
        workspace_id=row["workspace_id"],
        thread_id=row["thread_id"],
        seq=row["seq"],
        role=MessageRole(row["role"]),
        content=row["content"],
        status=MessageStatus(row["status"]),
        client_message_id=row["client_message_id"],
        model=row["model"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        cost_microcents=row["cost_microcents"],
        grounded=row["grounded"],
        created_at=row["created_at"],
    )

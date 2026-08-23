"""Postgres-backed MemorySummaryStorePort implementation — pool-bound,
short-transaction-per-call, same pattern as
adapters/postgres/message_store.py (see its docstring for why
SendMessage's singleton can't use a held-open connection).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.ports.memory import MemorySummary


class PostgresMemorySummaryStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_by_thread(self, workspace_id: UUID, thread_id: UUID) -> MemorySummary | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            row = await conn.fetchrow(
                "SELECT id, workspace_id, thread_id, upto_seq, content, model, token_count, "
                "created_at, updated_at FROM memory_summaries "
                "WHERE workspace_id = $1 AND thread_id = $2",
                workspace_id,
                thread_id,
            )
            return _row_to_summary(row) if row is not None else None

    async def upsert(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        thread_id: UUID,
        upto_seq: int,
        content: str,
        model: str,
        token_count: int,
    ) -> MemorySummary:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            row = await conn.fetchrow(
                """
                INSERT INTO memory_summaries
                    (id, workspace_id, thread_id, upto_seq, content, model, token_count)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (thread_id) DO UPDATE SET
                    upto_seq = EXCLUDED.upto_seq,
                    content = EXCLUDED.content,
                    model = EXCLUDED.model,
                    token_count = EXCLUDED.token_count,
                    updated_at = now()
                RETURNING id, workspace_id, thread_id, upto_seq, content, model, token_count,
                          created_at, updated_at
                """,
                id,
                workspace_id,
                thread_id,
                upto_seq,
                content,
                model,
                token_count,
            )
            assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row
            return _row_to_summary(row)


def _row_to_summary(row: asyncpg.Record) -> MemorySummary:
    return MemorySummary(
        id=row["id"],
        workspace_id=row["workspace_id"],
        thread_id=row["thread_id"],
        upto_seq=row["upto_seq"],
        content=row["content"],
        model=row["model"],
        token_count=row["token_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

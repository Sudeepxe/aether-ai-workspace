"""Postgres-backed MessageStorePort implementation (see the port's
docstring for why this is pool-bound with a fresh connection/transaction
per call, unlike the request-scoped ``Postgres*Repository`` adapters).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from aether.adapters.postgres.citation_repository import PostgresCitationRepository
from aether.adapters.postgres.message_repository import PostgresMessageRepository
from aether.ports.chat import Citation, CitationDraft, Message, MessageRole, MessageStatus


class PostgresMessageStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def persist(
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
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            return await PostgresMessageRepository(conn).create(
                id=id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                role=role,
                content=content,
                status=status,
                client_message_id=client_message_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_microcents=cost_microcents,
                grounded=grounded,
            )

    async def find_by_client_message_id(
        self, workspace_id: UUID, thread_id: UUID, client_message_id: str
    ) -> Message | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            return await PostgresMessageRepository(conn).get_by_client_message_id(
                workspace_id, thread_id, client_message_id
            )

    async def find_by_seq(self, workspace_id: UUID, thread_id: UUID, seq: int) -> Message | None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            return await PostgresMessageRepository(conn).get_by_seq(workspace_id, thread_id, seq)

    async def recent(self, workspace_id: UUID, thread_id: UUID, *, limit: int) -> list[Message]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            newest_first = await PostgresMessageRepository(conn).list_by_thread(
                workspace_id, thread_id, after_seq=None, limit=limit
            )
            return list(reversed(newest_first))

    async def persist_with_citations(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        thread_id: UUID,
        content: str,
        status: MessageStatus,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cost_microcents: int | None,
        citations: list[CitationDraft],
    ) -> tuple[Message, list[Citation]]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            message = await PostgresMessageRepository(conn).create(
                id=id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                role=MessageRole.ASSISTANT,
                content=content,
                status=status,
                client_message_id=None,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_microcents=cost_microcents,
                grounded=True,
            )
            created_citations = await PostgresCitationRepository(conn).create_many(
                workspace_id, message.id, citations
            )
            return message, created_citations

    async def list_citations(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(workspace_id))
            return await PostgresCitationRepository(conn).list_by_message(workspace_id, message_id)

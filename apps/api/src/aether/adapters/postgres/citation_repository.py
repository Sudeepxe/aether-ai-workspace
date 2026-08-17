"""Postgres-backed CitationRepositoryPort implementation.

Connection-bound (see message_repository.py's docstring for the same
pattern) — issue #60 composes ``create_many`` into the same
transaction as the assistant message's own persist call, so a citation
row can never exist for a message that failed to persist, or vice
versa.

Citation ids are generated here (plain ``uuid4()``, no determinism
needed) rather than passed in by the caller, unlike
``ThreadRepositoryPort.create()``'s IdPort-supplied id — citations are
write-once with no at-least-once-redelivery path to make idempotent
against (unlike the ingestion pipeline's deterministic chunk ids), so
there's nothing a caller-supplied id would buy here.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg

from aether.ports.repositories import Citation, CitationDraft


class PostgresCitationRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create_many(
        self, workspace_id: UUID, message_id: UUID, citations: list[CitationDraft]
    ) -> list[Citation]:
        if not citations:
            return []
        rows = await self._conn.fetch(
            """
            INSERT INTO message_citations (
                id, workspace_id, message_id, chunk_id, document_title,
                section_path, page_start, page_end
            )
            SELECT * FROM unnest(
                $1::uuid[], $2::uuid[], $3::uuid[], $4::uuid[], $5::text[],
                $6::text[], $7::int[], $8::int[]
            )
            RETURNING id, workspace_id, message_id, chunk_id, document_title,
                      section_path, page_start, page_end, created_at
            """,
            [uuid4() for _ in citations],
            [workspace_id for _ in citations],
            [message_id for _ in citations],
            [c.chunk_id for c in citations],
            [c.document_title for c in citations],
            [c.section_path for c in citations],
            [c.page_start for c in citations],
            [c.page_end for c in citations],
        )
        return [_row_to_citation(row) for row in rows]

    async def list_by_message(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        rows = await self._conn.fetch(
            "SELECT id, workspace_id, message_id, chunk_id, document_title, section_path, "
            "page_start, page_end, created_at FROM message_citations "
            "WHERE workspace_id = $1 AND message_id = $2 ORDER BY created_at",
            workspace_id,
            message_id,
        )
        return [_row_to_citation(row) for row in rows]


def _row_to_citation(row: asyncpg.Record) -> Citation:
    return Citation(
        id=row["id"],
        workspace_id=row["workspace_id"],
        message_id=row["message_id"],
        chunk_id=row["chunk_id"],
        document_title=row["document_title"],
        section_path=row["section_path"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        created_at=row["created_at"],
    )

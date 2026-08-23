from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aether.domain.entities import Message
from aether.ports.memory import MemorySummary


class FakeMemorySummaryStore:
    def __init__(self) -> None:
        self._rows: dict[UUID, MemorySummary] = {}  # keyed by thread_id

    async def get_by_thread(self, workspace_id: UUID, thread_id: UUID) -> MemorySummary | None:
        row = self._rows.get(thread_id)
        return row if row is not None and row.workspace_id == workspace_id else None

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
        summary = MemorySummary(
            id=id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            upto_seq=upto_seq,
            content=content,
            model=model,
            token_count=token_count,
            created_at=datetime.now().astimezone(),
            updated_at=datetime.now().astimezone(),
        )
        self._rows[thread_id] = summary
        return summary


class FakeMemoryCompactor:
    def __init__(self, *, summary: str = "fake summary", error: Exception | None = None) -> None:
        self._summary = summary
        self._error = error
        self.calls: list[tuple[str | None, list[Message]]] = []

    async def summarize(
        self, *, previous_summary: str | None, messages_to_compact: list[Message]
    ) -> str:
        self.calls.append((previous_summary, messages_to_compact))
        if self._error is not None:
            raise self._error
        return self._summary

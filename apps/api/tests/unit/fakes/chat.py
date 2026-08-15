from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from aether.domain.entities import Message, MessageRole, MessageStatus, Thread
from aether.ports.streaming import BufferedEvent


class FakeThreadRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, Thread] = {}

    async def create(
        self, *, id: UUID, workspace_id: UUID, created_by: UUID, title: str | None
    ) -> Thread:
        now = datetime.now().astimezone()
        thread = Thread(
            id=id,
            workspace_id=workspace_id,
            created_by=created_by,
            title=title,
            settings={},
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._rows[id] = thread
        return thread

    async def get(self, workspace_id: UUID, thread_id: UUID) -> Thread | None:
        thread = self._rows.get(thread_id)
        if thread is None or thread.workspace_id != workspace_id or thread.deleted_at is not None:
            return None
        return thread

    async def list_by_workspace(
        self, workspace_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[Thread]:
        items = [
            t
            for t in self._rows.values()
            if t.workspace_id == workspace_id and t.deleted_at is None
        ]
        items.sort(key=lambda t: (t.created_at, t.id), reverse=True)
        if after is not None:
            items = [t for t in items if (t.created_at, t.id) < after]
        return items[:limit]

    async def update_title(
        self, workspace_id: UUID, thread_id: UUID, *, title: str | None
    ) -> Thread | None:
        current = await self.get(workspace_id, thread_id)
        if current is None:
            return None
        updated = Thread(
            id=current.id,
            workspace_id=current.workspace_id,
            created_by=current.created_by,
            title=title,
            settings=current.settings,
            created_at=current.created_at,
            updated_at=datetime.now().astimezone(),
            deleted_at=None,
        )
        self._rows[thread_id] = updated
        return updated

    async def soft_delete(
        self, workspace_id: UUID, thread_id: UUID, *, deleted_at: datetime
    ) -> None:
        current = self._rows.get(thread_id)
        if current is not None and current.workspace_id == workspace_id:
            self._rows[thread_id] = Thread(
                id=current.id,
                workspace_id=current.workspace_id,
                created_by=current.created_by,
                title=current.title,
                settings=current.settings,
                created_at=current.created_at,
                updated_at=current.updated_at,
                deleted_at=deleted_at,
            )


class FakeMessageStore:
    """Backs both MessageRepositoryPort (thread routes' list/get) and
    MessageStorePort (the orchestrator) in tests — a real deployment
    splits these across connection-bound vs pool-bound adapters (see
    ports.chat.MessageStorePort's docstring), but a single in-memory dict
    is enough to exercise both call shapes without duplicating fakes."""

    def __init__(self) -> None:
        self._rows: dict[UUID, Message] = {}
        self._next_seq: dict[UUID, int] = {}

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
        seq = self._next_seq.get(thread_id, 1)
        self._next_seq[thread_id] = seq + 1
        message = Message(
            id=id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            seq=seq,
            role=role,
            content=content,
            status=status,
            client_message_id=client_message_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_microcents=cost_microcents,
            grounded=grounded,
            created_at=datetime.now().astimezone(),
        )
        self._rows[id] = message
        return message

    persist = create  # MessageStorePort's method name; identical semantics here

    async def get_by_client_message_id(
        self, workspace_id: UUID, thread_id: UUID, client_message_id: str
    ) -> Message | None:
        for m in self._rows.values():
            if (
                m.workspace_id == workspace_id
                and m.thread_id == thread_id
                and m.client_message_id == client_message_id
            ):
                return m
        return None

    find_by_client_message_id = get_by_client_message_id

    async def get_by_seq(self, workspace_id: UUID, thread_id: UUID, seq: int) -> Message | None:
        for m in self._rows.values():
            if m.workspace_id == workspace_id and m.thread_id == thread_id and m.seq == seq:
                return m
        return None

    find_by_seq = get_by_seq

    async def list_by_thread(
        self, workspace_id: UUID, thread_id: UUID, *, after_seq: int | None, limit: int
    ) -> list[Message]:
        items = [
            m
            for m in self._rows.values()
            if m.workspace_id == workspace_id and m.thread_id == thread_id
        ]
        items.sort(key=lambda m: m.seq, reverse=True)
        if after_seq is not None:
            items = [m for m in items if m.seq < after_seq]
        return items[:limit]

    async def recent(self, workspace_id: UUID, thread_id: UUID, *, limit: int) -> list[Message]:
        newest_first = await self.list_by_thread(
            workspace_id, thread_id, after_seq=None, limit=limit
        )
        return list(reversed(newest_first))


class FakeGenerator:
    """Deterministic, test-controlled token source — a simpler stand-in
    than EchoGenerator for unit tests that don't want word-splitting
    semantics muddying assertions."""

    def __init__(self, *, deltas: list[str]) -> None:
        self._deltas = deltas

    async def generate(
        self, *, thread_history: list[Message], user_content: str
    ) -> AsyncIterator[str]:
        for delta in self._deltas:
            yield delta


class FakeStreamBuffer:
    def __init__(self) -> None:
        self.events: dict[tuple[UUID, UUID], list[BufferedEvent]] = {}

    async def append(self, workspace_id: UUID, generation_id: UUID, event: BufferedEvent) -> None:
        self.events.setdefault((workspace_id, generation_id), []).append(event)

    async def read_after(
        self, workspace_id: UUID, generation_id: UUID, *, after_seq: int
    ) -> list[BufferedEvent]:
        return [e for e in self.events.get((workspace_id, generation_id), []) if e.seq > after_seq]


class _FakeSubscription:
    """Checks membership live against the parent's set on every call —
    not a value snapshotted at subscription time — so a test can mark a
    generation cancelled *between* pulls on the orchestrator's async
    generator and see the next iteration observe it, exactly like a real
    pub/sub message arriving mid-stream."""

    def __init__(self, cancelled_generations: set[UUID], generation_id: UUID) -> None:
        self._cancelled_generations = cancelled_generations
        self._generation_id = generation_id

    def is_cancelled(self) -> bool:
        return self._generation_id in self._cancelled_generations


class FakeCancellation:
    def __init__(self) -> None:
        self.cancelled_generations: set[UUID] = set()
        self.published: list[tuple[UUID, UUID]] = []

    async def publish_cancel(self, workspace_id: UUID, generation_id: UUID) -> None:
        self.published.append((workspace_id, generation_id))
        self.cancelled_generations.add(generation_id)

    @asynccontextmanager
    async def subscription(
        self, workspace_id: UUID, generation_id: UUID
    ) -> AsyncIterator[_FakeSubscription]:
        yield _FakeSubscription(self.cancelled_generations, generation_id)

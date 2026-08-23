from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

from aether.domain.entities import (
    Citation,
    CitationDraft,
    Message,
    MessageRole,
    MessageStatus,
    Thread,
    UsageEvent,
)
from aether.ports.chat import GenerationUsage, GeneratorChunk, RetrievedContext
from aether.ports.metering import AdmissionDecision, UsageEventKind, UsageModelRollup, UsageRollup
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
        self._citations: dict[UUID, list[Citation]] = {}

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
        message = await self.create(
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
        created = [
            Citation(
                id=uuid4(),
                workspace_id=workspace_id,
                message_id=message.id,
                chunk_id=draft.chunk_id,
                document_title=draft.document_title,
                section_path=draft.section_path,
                page_start=draft.page_start,
                page_end=draft.page_end,
                created_at=datetime.now().astimezone(),
            )
            for draft in citations
        ]
        self._citations[message.id] = created
        return message, created

    async def list_citations(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        return [c for c in self._citations.get(message_id, []) if c.workspace_id == workspace_id]


class FakeCitationRepository:
    """Standalone CitationRepositoryPort fake — used where a test wants a
    citation store independent of FakeMessageStore's own internal
    bookkeeping (e.g. ListMessages, which composes both ports)."""

    def __init__(self) -> None:
        self._rows: dict[UUID, list[Citation]] = {}

    def seed(self, message_id: UUID, citations: list[Citation]) -> None:
        self._rows[message_id] = citations

    async def create_many(
        self, workspace_id: UUID, message_id: UUID, citations: list[CitationDraft]
    ) -> list[Citation]:
        created = [
            Citation(
                id=uuid4(),
                workspace_id=workspace_id,
                message_id=message_id,
                chunk_id=d.chunk_id,
                document_title=d.document_title,
                section_path=d.section_path,
                page_start=d.page_start,
                page_end=d.page_end,
                created_at=datetime.now().astimezone(),
            )
            for d in citations
        ]
        self._rows[message_id] = created
        return created

    async def list_by_message(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        return [c for c in self._rows.get(message_id, []) if c.workspace_id == workspace_id]

    async def list_by_messages(self, workspace_id: UUID, message_ids: list[UUID]) -> list[Citation]:
        return [
            c
            for message_id in message_ids
            for c in self._rows.get(message_id, [])
            if c.workspace_id == workspace_id
        ]


_DEFAULT_USAGE = GenerationUsage(
    prompt_tokens=1, completion_tokens=1, cost_microcents=100, model="fake-model"
)


class FakeGenerator:
    """Deterministic, test-controlled token source — a simpler stand-in
    than EchoGenerator for unit tests that don't want word-splitting
    semantics muddying assertions. Yields a GenerationUsage after all
    deltas (matching every real GeneratorPort implementation's contract)
    unless ``usage=None``; optionally raises ``error`` after
    ``fail_after`` deltas, to exercise the orchestrator's pre-first-token
    vs mid-stream failure handling."""

    def __init__(
        self,
        *,
        deltas: list[str],
        usage: GenerationUsage | None = _DEFAULT_USAGE,
        error: Exception | None = None,
        fail_after: int = 0,
    ) -> None:
        self._deltas = deltas
        self._usage = usage
        self._error = error
        self._fail_after = fail_after
        self.contexts_seen: list[RetrievedContext | None] = []

    @property
    def primary_model(self) -> str:
        return "fake-model"

    async def generate(
        self,
        *,
        thread_history: list[Message],
        user_content: str,
        context: RetrievedContext | None = None,
    ) -> AsyncIterator[GeneratorChunk]:
        self.contexts_seen.append(context)
        for i, delta in enumerate(self._deltas):
            if self._error is not None and i == self._fail_after:
                raise self._error
            yield delta
        if self._error is not None and self._fail_after >= len(self._deltas):
            raise self._error
        if self._usage is not None:
            yield self._usage


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


class FakeBudgetAdmission:
    """Allows everything by default — tests that want a refusal set
    ``allow_workspace=False`` or ``allow_global=False`` explicitly, so a
    test's intent is visible at the call site rather than buried in a
    fixture default."""

    def __init__(
        self,
        *,
        allow_global: bool = True,
        allow_workspace: bool = True,
        settled_microcents: int = 0,
        monthly_limit_microcents: int = 500_000_000,
    ) -> None:
        self.allow_global = allow_global
        self.allow_workspace = allow_workspace
        self.settled_microcents = settled_microcents
        self.monthly_limit_microcents = monthly_limit_microcents
        self.checked_workspaces: list[UUID] = []
        self.global_checks = 0

    async def check(self, workspace_id: UUID, *, ceiling_microcents: int) -> AdmissionDecision:
        self.checked_workspaces.append(workspace_id)
        return AdmissionDecision(
            allowed=self.allow_workspace,
            soft_limit_crossed=False,
            settled_microcents=self.settled_microcents,
            monthly_limit_microcents=self.monthly_limit_microcents,
        )

    async def check_global(self, *, ceiling_microcents: int) -> bool:
        self.global_checks += 1
        return self.allow_global


class FakeUsageLedger:
    def __init__(self) -> None:
        self.recorded: list[UsageEvent] = []

    async def record(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        user_id: UUID | None,
        kind: UsageEventKind,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_microcents: int,
        generation_id: UUID | None,
    ) -> UsageEvent:
        event = UsageEvent(
            id=id,
            workspace_id=workspace_id,
            user_id=user_id,
            kind=kind,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_microcents=cost_microcents,
            generation_id=generation_id,
            occurred_at=datetime.now().astimezone(),
        )
        self.recorded.append(event)
        return event

    async def rollup(self, workspace_id: UUID, *, since: datetime) -> UsageRollup:
        matching = [e for e in self.recorded if e.workspace_id == workspace_id]
        by_model: dict[str, UsageModelRollup] = {}
        for e in matching:
            current = by_model.get(
                e.model,
                UsageModelRollup(
                    model=e.model,
                    request_count=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    cost_microcents=0,
                ),
            )
            by_model[e.model] = UsageModelRollup(
                model=e.model,
                request_count=current.request_count + 1,
                prompt_tokens=current.prompt_tokens + e.prompt_tokens,
                completion_tokens=current.completion_tokens + e.completion_tokens,
                cost_microcents=current.cost_microcents + e.cost_microcents,
            )
        return UsageRollup(
            workspace_id=workspace_id,
            period_start=since,
            by_model=list(by_model.values()),
            total_cost_microcents=sum(e.cost_microcents for e in matching),
        )

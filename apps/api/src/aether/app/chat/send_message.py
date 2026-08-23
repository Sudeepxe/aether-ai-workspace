"""ChatTurnCommand -> SSE token stream -> persisted message (§3.2.3,
§4.4). S3's ``EchoGenerator`` and S4's real ``LlmRouter`` are both valid
``GeneratorPort`` implementations — this use case is provider-agnostic by
construction, so wiring in the real router (issue #38) needed no changes
here beyond handling the two things EchoGenerator never exercised: a
generator that can actually raise (real provider failures), and a
generator whose final usage carries a real, non-zero cost.

Grounded by default since issue #60 (ADR-6.4, ADR-6.3): every non-replay
turn runs query rewrite -> hybrid retrieval -> Gate 1 before generation.
Gate 1 failing (no chunks, or top score below threshold) short-circuits
straight to a canned refusal — no generator call, ``grounded=False``,
zero citations, zero cost — matching §3.2.5's "an empty retrieval is a
feature, not an error." Gate 1 passing calls the generator with the
retrieved context (ADR-6.4's Gate 2 protocol) and persists one citation
per retrieved chunk, in the same transaction as the assistant message
(``MessageStorePort.persist_with_citations``).

Retrieved chunk content is untrusted (§2's untrusted-content principle):
it originates from uploaded documents, not the caller, so injected
instructions inside a chunk must never override the assistant's actual
system prompt — the grounded system prompt (app/llm/router.py's
``_GROUNDED_SYSTEM_PROMPT``) frames chunks as data to answer from, never
as instructions to follow. Full injection-defense tooling (e.g.
detecting/stripping suspicious chunk content) is a later-sprint concern;
this orchestrator's job is only to never blur that boundary itself.

Budget admission (§3.2.14) happens once, before the user's turn is even
persisted — there's no point recording a message the caller is about to
be refused for. Settlement happens once, after generation completes,
straight to Postgres (not via an async worker consumer — see
adapters.postgres.usage_ledger's module docstring for why a direct,
per-event settlement is still correct, just not throughput-optimized for
extreme burst on one workspace's budget row).

Streaming session state (§3.2.3, Ch.3 F-3): every event this use case
yields — on the normal generation path *and* the idempotent-replay path
— is also appended to the Redis buffer under one shared per-generation
seq counter, since the HTTP layer only ever reads from that buffer (see
http/sse.py's ``follow_buffer``), never from this generator directly.
The streaming loop also checks the cancellation subscription after every
token — the two mechanisms that make Last-Event-ID resume and
DELETE-to-cancel work regardless of which replica a reconnect or cancel
request lands on.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from aether.app.retrieval.hybrid_search import HybridSearch, RankedChunk
from aether.app.retrieval.query_rewrite import QueryRewriter
from aether.app.retrieval.refusal_gate import RetrievalGate
from aether.domain.entities import Message, MessageRole, MessageStatus
from aether.domain.errors import BudgetExhaustedError
from aether.domain.streaming import (
    CitationStreamEvent,
    DoneStreamEvent,
    ErrorStreamEvent,
    GenerationStatus,
    MetaStreamEvent,
    StreamEvent,
    TokenStreamEvent,
    UsageStreamEvent,
    event_payload,
    event_type_name,
)
from aether.ports.chat import (
    NOT_IN_KNOWLEDGE_BASE_REPLY,
    Citation,
    CitationDraft,
    GenerationUsage,
    GeneratorPort,
    MessageStorePort,
    RetrievedContext,
    RetrievedContextChunk,
)
from aether.ports.llm import ProviderError
from aether.ports.metering import BudgetAdmissionPort, UsageEventKind, UsageLedgerPort
from aether.ports.security import IdPort
from aether.ports.streaming import BufferedEvent, CancellationPort, StreamBufferPort

_HISTORY_LIMIT = 20  # recent messages passed as generator context


@dataclass(frozen=True, slots=True)
class SendMessageCommand:
    workspace_id: UUID
    thread_id: UUID
    content: str
    client_message_id: str


class SendMessage:
    def __init__(
        self,
        *,
        messages: MessageStorePort,
        generator: GeneratorPort,
        hybrid_search: HybridSearch,
        query_rewriter: QueryRewriter,
        retrieval_gate: RetrievalGate,
        buffer: StreamBufferPort,
        cancellation: CancellationPort,
        admission: BudgetAdmissionPort,
        usage_ledger: UsageLedgerPort,
        ids: IdPort,
        max_tokens: int,
        ceiling_cost_per_1k_microcents: int,
    ) -> None:
        self._messages = messages
        self._generator = generator
        self._hybrid_search = hybrid_search
        self._query_rewriter = query_rewriter
        self._retrieval_gate = retrieval_gate
        self._buffer = buffer
        self._cancellation = cancellation
        self._admission = admission
        self._usage_ledger = usage_ledger
        self._ids = ids
        self._max_tokens = max_tokens
        self._ceiling_cost_per_1k_microcents = ceiling_cost_per_1k_microcents

    async def execute(self, command: SendMessageCommand) -> AsyncIterator[StreamEvent]:
        generation_id = self._ids.new_id()

        async def buffer_and_yield(event: StreamEvent) -> StreamEvent:
            payload = json.dumps(event_payload(event))
            await self._buffer.append(
                command.workspace_id,
                generation_id,
                BufferedEvent(seq=event.seq, event_type=event_type_name(event), data=payload),
            )
            return event

        existing_user_message = await self._messages.find_by_client_message_id(
            command.workspace_id, command.thread_id, command.client_message_id
        )
        if existing_user_message is not None:
            # Idempotent replay (ADR-4.6): a retried POST with the same
            # client-generated message_id must not re-persist the turn,
            # regenerate a new reply, re-run retrieval, or re-check the
            # budget (no new cost is incurred by a replay).
            existing_reply = await self._messages.find_by_seq(
                command.workspace_id, command.thread_id, existing_user_message.seq + 1
            )
            if existing_reply is not None:
                existing_citations = (
                    await self._messages.list_citations(command.workspace_id, existing_reply.id)
                    if existing_reply.grounded
                    else []
                )
                for event in _replay_events(
                    existing_reply, citations=existing_citations, generation_id=generation_id
                ):
                    yield await buffer_and_yield(event)
                return
        else:
            ceiling = self._ceiling_microcents(command.content)
            if not await self._admission.check_global(ceiling_microcents=ceiling):
                raise BudgetExhaustedError("global monthly budget exhausted")
            decision = await self._admission.check(command.workspace_id, ceiling_microcents=ceiling)
            if not decision.allowed:
                raise BudgetExhaustedError(
                    f"workspace budget exhausted: {decision.settled_microcents}/"
                    f"{decision.monthly_limit_microcents} microcents settled"
                )

            await self._messages.persist(
                id=self._ids.new_id(),
                workspace_id=command.workspace_id,
                thread_id=command.thread_id,
                role=MessageRole.USER,
                content=command.content,
                status=MessageStatus.COMPLETE,
                client_message_id=command.client_message_id,
            )

        history = await self._messages.recent(
            command.workspace_id, command.thread_id, limit=_HISTORY_LIMIT
        )
        # `history`'s last entry is the user turn just persisted above —
        # the rewriter wants only *prior* turns to condense against.
        dual_feed_query = await self._query_rewriter.build_dual_feed_query(
            history=history[:-1], raw_query=command.content
        )
        retrieval_result = await self._hybrid_search.search(
            command.workspace_id,
            query=dual_feed_query.vector_query,
            lexical_queries=dual_feed_query.lexical_queries,
        )
        gate_decision = self._retrieval_gate.evaluate(retrieval_result)
        grounded = gate_decision.passed

        seq = 0
        yield await buffer_and_yield(
            MetaStreamEvent(
                generation_id=generation_id,
                seq=seq,
                model=self._generator.primary_model,
                grounded=grounded,
            )
        )
        seq += 1

        if not grounded:
            # Gate 1 refusal (ADR-6.4, §3.2.5): no chunks cleared the
            # threshold — no generator call, no hallucination risk. A
            # cheap, designed outcome, not an error.
            yield await buffer_and_yield(
                TokenStreamEvent(
                    generation_id=generation_id, seq=seq, delta=NOT_IN_KNOWLEDGE_BASE_REPLY
                )
            )
            seq += 1
            await self._messages.persist(
                id=self._ids.new_id(),
                workspace_id=command.workspace_id,
                thread_id=command.thread_id,
                role=MessageRole.ASSISTANT,
                content=NOT_IN_KNOWLEDGE_BASE_REPLY,
                status=MessageStatus.COMPLETE,
                client_message_id=None,
                model=self._generator.primary_model,
                prompt_tokens=0,
                completion_tokens=0,
                cost_microcents=0,
                grounded=False,
            )
            yield await buffer_and_yield(
                UsageStreamEvent(
                    generation_id=generation_id,
                    seq=seq,
                    prompt_tokens=0,
                    completion_tokens=0,
                    cost_microcents=0,
                )
            )
            seq += 1
            yield await buffer_and_yield(
                DoneStreamEvent(
                    generation_id=generation_id, seq=seq, status=GenerationStatus.COMPLETE
                )
            )
            return

        context = RetrievedContext(
            chunks=[_to_context_chunk(chunk) for chunk in retrieval_result.chunks]
        )

        accumulated = ""
        status = GenerationStatus.COMPLETE
        error_code: str | None = None
        error_message: str | None = None
        final_model = self._generator.primary_model
        prompt_tokens = 0
        completion_tokens = 0
        cost_microcents = 0

        async with self._cancellation.subscription(
            command.workspace_id, generation_id
        ) as cancel_sub:
            try:
                async for chunk in self._generator.generate(
                    thread_history=history, user_content=command.content, context=context
                ):
                    if cancel_sub.is_cancelled():
                        status = GenerationStatus.CANCELLED
                        break
                    if isinstance(chunk, GenerationUsage):
                        prompt_tokens = chunk.prompt_tokens
                        completion_tokens = chunk.completion_tokens
                        cost_microcents = chunk.cost_microcents
                        final_model = chunk.model
                        continue
                    accumulated += chunk
                    yield await buffer_and_yield(
                        TokenStreamEvent(generation_id=generation_id, seq=seq, delta=chunk)
                    )
                    seq += 1
                else:
                    # Loop completed without a break: check one more time
                    # in case cancellation arrived after the last chunk
                    # but before the generator's natural exhaustion.
                    if cancel_sub.is_cancelled():
                        status = GenerationStatus.CANCELLED
            except ProviderError as exc:
                status = GenerationStatus.PARTIAL if accumulated else GenerationStatus.ERROR
                error_code = "provider_error"
                error_message = str(exc)
            except Exception as exc:
                # A real failure must still settle into a defined
                # terminal state, not crash the background task silently
                # and leave a client's follow_buffer poll hanging forever.
                status = GenerationStatus.PARTIAL if accumulated else GenerationStatus.ERROR
                error_code = "generation_failed"
                error_message = str(exc)

        if error_message is not None:
            yield await buffer_and_yield(
                ErrorStreamEvent(
                    generation_id=generation_id,
                    seq=seq,
                    code=error_code or "generation_failed",
                    message=error_message,
                )
            )
            seq += 1

        if accumulated == "" and status in (GenerationStatus.CANCELLED, GenerationStatus.ERROR):
            pass  # nothing worth persisting
        else:
            assistant_status = (
                MessageStatus.CANCELLED
                if status is GenerationStatus.CANCELLED
                else MessageStatus.COMPLETE
                if status is GenerationStatus.COMPLETE
                else MessageStatus.PARTIAL
            )
            citation_drafts = [
                CitationDraft(
                    chunk_id=chunk.chunk_id,
                    document_title=chunk.document_title,
                    section_path=chunk.section_path,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
                for chunk in retrieval_result.chunks
            ]
            _assistant_message, created_citations = await self._messages.persist_with_citations(
                id=self._ids.new_id(),
                workspace_id=command.workspace_id,
                thread_id=command.thread_id,
                content=accumulated,
                status=assistant_status,
                model=final_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_microcents=cost_microcents,
                citations=citation_drafts,
            )
            for citation in created_citations:
                yield await buffer_and_yield(_citation_event(citation, generation_id, seq))
                seq += 1
            if cost_microcents > 0:
                await self._usage_ledger.record(
                    id=self._ids.new_id(),
                    workspace_id=command.workspace_id,
                    user_id=None,
                    kind=UsageEventKind.CHAT,
                    model=final_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_microcents=cost_microcents,
                    generation_id=generation_id,
                )

        yield await buffer_and_yield(
            UsageStreamEvent(
                generation_id=generation_id,
                seq=seq,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_microcents=cost_microcents,
            )
        )
        seq += 1
        yield await buffer_and_yield(
            DoneStreamEvent(generation_id=generation_id, seq=seq, status=status)
        )

    def _ceiling_microcents(self, content: str) -> int:
        """Local prompt-token estimate (word count — same honest-estimate
        posture as EchoGenerator's usage) plus the max_tokens ceiling,
        priced at a conservative worst-case rate (§3.2.14: "prompt tokens
        counted locally + max_tokens ceiling"). This is deliberately
        provider-agnostic — the orchestrator doesn't know which provider
        in the fallback chain will actually serve the request."""
        local_prompt_estimate = len(content.split())
        return (
            (local_prompt_estimate + self._max_tokens)
            * self._ceiling_cost_per_1k_microcents
            // 1000
        )


def _to_context_chunk(chunk: RankedChunk) -> RetrievedContextChunk:
    return RetrievedContextChunk(
        content=chunk.content, document_title=chunk.document_title, section_path=chunk.section_path
    )


def _citation_event(citation: Citation, generation_id: UUID, seq: int) -> CitationStreamEvent:
    return CitationStreamEvent(
        generation_id=generation_id,
        seq=seq,
        citation_id=citation.id,
        chunk_id=citation.chunk_id,
        document_title=citation.document_title,
        section_path=citation.section_path,
        page_start=citation.page_start,
        page_end=citation.page_end,
    )


def _replay_events(
    existing_reply: Message, *, citations: list[Citation], generation_id: UUID
) -> list[StreamEvent]:
    events: list[StreamEvent] = [
        MetaStreamEvent(
            generation_id=generation_id,
            seq=0,
            model=existing_reply.model or "unknown",
            grounded=existing_reply.grounded,
        ),
        TokenStreamEvent(generation_id=generation_id, seq=1, delta=existing_reply.content),
    ]
    seq = 2
    for citation in citations:
        events.append(_citation_event(citation, generation_id, seq))
        seq += 1
    events.append(
        UsageStreamEvent(
            generation_id=generation_id,
            seq=seq,
            prompt_tokens=existing_reply.prompt_tokens or 0,
            completion_tokens=existing_reply.completion_tokens or 0,
            cost_microcents=existing_reply.cost_microcents or 0,
        )
    )
    seq += 1
    events.append(
        DoneStreamEvent(generation_id=generation_id, seq=seq, status=GenerationStatus.COMPLETE)
    )
    return events

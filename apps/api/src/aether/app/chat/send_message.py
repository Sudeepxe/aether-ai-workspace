"""ChatTurnCommand -> SSE token stream -> persisted message (§3.2.3,
§4.4). S3's ``EchoGenerator`` and S4's real ``LlmRouter`` are both valid
``GeneratorPort`` implementations — this use case is provider-agnostic by
construction, so wiring in the real router (issue #38) needed no changes
here beyond handling the two things EchoGenerator never exercised: a
generator that can actually raise (real provider failures), and a
generator whose final usage carries a real, non-zero cost.

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

from aether.domain.entities import Message, MessageRole, MessageStatus
from aether.domain.errors import BudgetExhaustedError
from aether.domain.streaming import (
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
from aether.ports.chat import GenerationUsage, GeneratorPort, MessageStorePort
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
            # regenerate a new reply, or re-check the budget (no new
            # cost is incurred by a replay).
            existing_reply = await self._messages.find_by_seq(
                command.workspace_id, command.thread_id, existing_user_message.seq + 1
            )
            if existing_reply is not None:
                for event in _replay_events(existing_reply, generation_id=generation_id):
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
        seq = 0

        yield await buffer_and_yield(
            MetaStreamEvent(
                generation_id=generation_id,
                seq=seq,
                model=self._generator.primary_model,
                grounded=False,
            )
        )
        seq += 1

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
                    thread_history=history, user_content=command.content
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
            await self._messages.persist(
                id=self._ids.new_id(),
                workspace_id=command.workspace_id,
                thread_id=command.thread_id,
                role=MessageRole.ASSISTANT,
                content=accumulated,
                status=assistant_status,
                client_message_id=None,
                model=final_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_microcents=cost_microcents,
                grounded=False,
            )
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


def _replay_events(existing_reply: Message, *, generation_id: UUID) -> list[StreamEvent]:
    return [
        MetaStreamEvent(
            generation_id=generation_id,
            seq=0,
            model=existing_reply.model or "unknown",
            grounded=False,
        ),
        TokenStreamEvent(generation_id=generation_id, seq=1, delta=existing_reply.content),
        UsageStreamEvent(
            generation_id=generation_id,
            seq=2,
            prompt_tokens=existing_reply.prompt_tokens or 0,
            completion_tokens=existing_reply.completion_tokens or 0,
            cost_microcents=existing_reply.cost_microcents or 0,
        ),
        DoneStreamEvent(generation_id=generation_id, seq=3, status=GenerationStatus.COMPLETE),
    ]

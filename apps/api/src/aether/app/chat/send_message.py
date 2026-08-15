"""ChatTurnCommand -> SSE token stream -> persisted message (issue #26,
§3.2.3, §4.4). ``EchoGenerator`` stands in for the real LLM Router (S4).

Streaming session state (§3.2.3, Ch.3 F-3): every event this use case
yields — on the normal generation path *and* the idempotent-replay path
— is also appended to the Redis buffer under one shared per-generation
seq counter. This isn't optional bookkeeping: the HTTP layer's response
body never reads from this generator directly (see http/sse.py's
``follow_buffer``); it always reads from the buffer, so any event this
method yields without buffering it would be invisible to the actual
caller — a real bug this file used to have on the replay path (an SSE
response that polled a buffer nothing ever wrote to and hung forever).
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
from aether.domain.streaming import (
    DoneStreamEvent,
    GenerationStatus,
    MetaStreamEvent,
    StreamEvent,
    TokenStreamEvent,
    UsageStreamEvent,
    event_payload,
    event_type_name,
)
from aether.ports.chat import GeneratorPort, MessageStorePort
from aether.ports.security import IdPort
from aether.ports.streaming import BufferedEvent, CancellationPort, StreamBufferPort

_HISTORY_LIMIT = 20  # recent messages passed as generator context
_ECHO_MODEL_NAME = "echo-v1"


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
        ids: IdPort,
    ) -> None:
        self._messages = messages
        self._generator = generator
        self._buffer = buffer
        self._cancellation = cancellation
        self._ids = ids

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
            # client-generated message_id must not re-persist the turn
            # or regenerate a new reply.
            existing_reply = await self._messages.find_by_seq(
                command.workspace_id, command.thread_id, existing_user_message.seq + 1
            )
            if existing_reply is not None:
                for event in _replay_events(existing_reply, generation_id=generation_id):
                    yield await buffer_and_yield(event)
                return
        else:
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
                generation_id=generation_id, seq=seq, model=_ECHO_MODEL_NAME, grounded=False
            )
        )
        seq += 1

        accumulated = ""
        status = GenerationStatus.COMPLETE
        async with self._cancellation.subscription(
            command.workspace_id, generation_id
        ) as cancel_sub:
            async for delta in self._generator.generate(
                thread_history=history, user_content=command.content
            ):
                if cancel_sub.is_cancelled():
                    status = GenerationStatus.CANCELLED
                    break
                accumulated += delta
                yield await buffer_and_yield(
                    TokenStreamEvent(generation_id=generation_id, seq=seq, delta=delta)
                )
                seq += 1
            else:
                # Loop completed without a break: check one more time in
                # case cancellation arrived after the last token but
                # before the generator's natural exhaustion.
                if cancel_sub.is_cancelled():
                    status = GenerationStatus.CANCELLED

        assistant_status = (
            MessageStatus.CANCELLED
            if status is GenerationStatus.CANCELLED
            else MessageStatus.COMPLETE
        )
        if accumulated == "" and status is GenerationStatus.CANCELLED:
            # Cancelled before any content was produced: nothing worth
            # persisting as a partial reply.
            pass
        else:
            await self._messages.persist(
                id=self._ids.new_id(),
                workspace_id=command.workspace_id,
                thread_id=command.thread_id,
                role=MessageRole.ASSISTANT,
                content=accumulated,
                status=assistant_status,
                client_message_id=None,
                model=_ECHO_MODEL_NAME,
                prompt_tokens=len(command.content.split()),
                completion_tokens=len(accumulated.split()),
                cost_microcents=0,
                grounded=False,
            )

        yield await buffer_and_yield(
            UsageStreamEvent(
                generation_id=generation_id,
                seq=seq,
                prompt_tokens=len(command.content.split()),
                completion_tokens=len(accumulated.split()),
                cost_microcents=0,
            )
        )
        seq += 1
        yield await buffer_and_yield(
            DoneStreamEvent(generation_id=generation_id, seq=seq, status=status)
        )


def _replay_events(existing_reply: Message, *, generation_id: UUID) -> list[StreamEvent]:
    return [
        MetaStreamEvent(
            generation_id=generation_id,
            seq=0,
            model=existing_reply.model or _ECHO_MODEL_NAME,
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

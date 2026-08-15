"""Chat-turn ports: the LLM Router interface and the streaming-safe
message store (Blueprint §3.2.3, §4.4).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aether.domain.entities import Message, MessageRole, MessageStatus

__all__ = [
    "GenerationUsage",
    "GeneratorChunk",
    "GeneratorPort",
    "Message",
    "MessageRole",
    "MessageStatus",
    "MessageStorePort",
]


@dataclass(frozen=True, slots=True)
class GenerationUsage:
    """The final item a generator yields, once available — real,
    provider-authoritative token counts for the LLM Router (§3.2.14:
    settlement is "from actual provider usage"); EchoGenerator's own
    word-count estimate (cost_microcents=0) for the S3 placeholder. Never
    a mid-stream item — exactly one, always last. Cost is computed here
    (by whichever generator has the model's pricing, not by the
    orchestrator) so SendMessage never needs to know about provider
    capability registries."""

    prompt_tokens: int
    completion_tokens: int
    cost_microcents: int
    model: str


# A tagged union rather than a separate return channel: an async
# generator can't yield *and* return a distinct value, so the usage
# record is just the final yielded item instead of a special one.
GeneratorChunk = str | GenerationUsage


class GeneratorPort(Protocol):
    """One internal interface over the completion engine. S3's only
    implementation was ``adapters.echo.generator.EchoGenerator``, a
    placeholder proving the streaming spine end-to-end before a real
    provider existed; S4 adds ``app.llm.router.LlmRouter`` as the real
    implementation, composing provider adapters behind circuit breakers
    and a fallback chain (issue #38)."""

    @property
    def primary_model(self) -> str:
        """A best-effort, pre-generation label for the SSE ``meta``
        event's ``model`` field, which must be emitted before generation
        starts (§4.4's grammar: meta is always first). Not authoritative
        — with a fallback chain, the model that actually answers may
        differ; the persisted message's ``model`` (from the final
        GenerationUsage) is the authoritative record."""
        ...

    def generate(
        self, *, thread_history: list[Message], user_content: str
    ) -> AsyncIterator[GeneratorChunk]:
        """Yields text deltas, then exactly one GenerationUsage as the
        final item. May raise (a real provider's failure modes —
        breaker-open, timeout, all-providers-down) — the orchestrator is
        responsible for turning that into a persisted partial message
        and an error event, not silently swallowing it."""
        ...


class MessageStorePort(Protocol):
    """Persists one message in its own short-lived, tenant-scoped
    connection/transaction — deliberately **not** bound to one
    connection held across a request's lifetime, unlike the other
    ``Postgres*Repository`` adapters (workspace/membership/thread/...).

    The streaming messages route's request lifetime spans a user-paced
    token stream that can run for seconds; holding one DB connection +
    transaction open for that whole duration would starve the connection
    pool under concurrent streams (§4.5's pool math: ~30 total server
    connections via PgBouncer). Each ``persist()`` call briefly acquires
    a connection, sets tenant context, writes, and releases — the user
    message and the final assistant message are two independent
    transactions, not one held across the generation.
    """

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
    ) -> Message: ...

    async def find_by_client_message_id(
        self, workspace_id: UUID, thread_id: UUID, client_message_id: str
    ) -> Message | None: ...

    async def find_by_seq(
        self, workspace_id: UUID, thread_id: UUID, seq: int
    ) -> Message | None: ...

    async def recent(self, workspace_id: UUID, thread_id: UUID, *, limit: int) -> list[Message]:
        """Most recent messages, oldest first — the generator's context
        window. Real context *management* (windowing under a token
        budget, summarization) is the Memory Service's job (§3.2.6,
        S8); until then the orchestrator passes this straight through."""
        ...

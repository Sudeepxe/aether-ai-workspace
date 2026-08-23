"""Chat-turn ports: the LLM Router interface and the streaming-safe
message store (Blueprint §3.2.3, §4.4).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aether.domain.entities import Citation, CitationDraft, Message, MessageRole, MessageStatus

__all__ = [
    "NOT_IN_KNOWLEDGE_BASE_REPLY",
    "Citation",
    "CitationDraft",
    "GenerationUsage",
    "GeneratorChunk",
    "GeneratorPort",
    "Message",
    "MessageRole",
    "MessageStatus",
    "MessageStorePort",
    "RetrievedContext",
    "RetrievedContextChunk",
]

NOT_IN_KNOWLEDGE_BASE_REPLY = "I don't have information about that in the knowledge base."
"""The single canonical refusal string for ADR-6.4's two-gate protocol —
both Gate 1 (issue #60's SendMessage, short-circuiting before any
generator call when retrieval clears no chunks/threshold) and Gate 2
(a generator's own defense when it's somehow invoked with empty
context anyway, e.g. adapters.echo.generator.EchoGenerator) return
this exact string, so a refusal reads identically regardless of which
gate produced it."""


@dataclass(frozen=True, slots=True)
class RetrievedContextChunk:
    """The minimal shape a generator needs to answer from a retrieved
    chunk — deliberately independent of app.retrieval.hybrid_search's
    RankedChunk (ports must not import from app, ADR per the layered
    architecture contract); the caller (issue #60's SendMessage) maps
    one to the other."""

    content: str
    document_title: str
    section_path: str


@dataclass(frozen=True, slots=True)
class RetrievedContext:
    """Present (even if ``chunks`` is empty) means "this is a grounded
    turn — answer only from these chunks, or say so explicitly if
    there's nothing relevant" (ADR-6.4's Gate 2 protocol). Absent means
    an ordinary, ungrounded turn — today's pre-S6 behavior, unchanged."""

    chunks: list[RetrievedContextChunk]


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
        self,
        *,
        thread_history: list[Message],
        user_content: str,
        context: RetrievedContext | None = None,
    ) -> AsyncIterator[GeneratorChunk]:
        """Yields text deltas, then exactly one GenerationUsage as the
        final item. May raise (a real provider's failure modes —
        breaker-open, timeout, all-providers-down) — the orchestrator is
        responsible for turning that into a persisted partial message
        and an error event, not silently swallowing it.

        ``context`` present (issue #58, ADR-6.4's Gate 2) means this
        turn must answer only from ``context.chunks`` — explicitly
        declining ("not in the knowledge base") if it can't, never
        falling back to the model's own general knowledge or to
        ``thread_history``. Absent means an ordinary ungrounded turn,
        unchanged from pre-S6 behavior."""
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
        """Persists a grounded assistant reply and its citations in one
        transaction (ADR-8.6, issue #60) — a citation row can never
        exist for a message that failed to persist, or vice versa.
        Assistant-only: ``role`` is implicitly ASSISTANT,
        ``client_message_id`` implicitly None, ``grounded`` implicitly
        True (only a Gate-1-passed turn ever calls this — a refusal or
        an ordinary/user message uses plain ``persist`` instead, which
        keeps its existing default ``grounded=False``)."""
        ...

    async def list_citations(self, workspace_id: UUID, message_id: UUID) -> list[Citation]:
        """Read path for the idempotent-replay path (``_replay_events``)
        to re-emit a previously-persisted grounded reply's citation
        events on reconnect/redelivery."""
        ...

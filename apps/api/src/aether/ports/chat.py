"""Chat-turn ports: the LLM placeholder interface and the streaming-safe
message store (Blueprint §3.2.3, §4.4).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID

from aether.domain.entities import Message, MessageRole, MessageStatus

__all__ = ["GeneratorPort", "Message", "MessageRole", "MessageStatus", "MessageStorePort"]


class GeneratorPort(Protocol):
    """One internal interface over the completion engine — the LLM
    Router lands in S4; this sprint's only implementation is
    ``adapters.echo.generator.EchoGenerator``, a placeholder that proves
    the streaming spine (persistence, SSE contract, cross-replica
    resume/cancel) end-to-end before a real provider is wired in
    (issue #26)."""

    def generate(self, *, thread_history: list[Message], user_content: str) -> AsyncIterator[str]:
        """Yields text deltas. Never raises for ordinary content — a real
        provider adapter's failure modes (breaker-open, timeout) are S4
        scope; this placeholder's only failure path is cancellation,
        handled by the orchestrator breaking out of iteration, not by
        the generator itself."""
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

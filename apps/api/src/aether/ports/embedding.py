"""Embedding provider port (§3.2.7, §6.2/D6-3): batched text -> vector
calls for the ingestion pipeline's embedding stage (issue #47). Kept as
its own port rather than folded into ``ports.llm``'s
``ProviderAdapterPort`` — embeddings and chat completions are different
capabilities with different request/response shapes, matching this
codebase's one-port-per-capability convention (``ports.malware_scan``,
``ports.storage``, ``ports.ingestion_repository``, ...).

``model``/``embedding_version`` are deliberately fixed adapter identity,
not per-call parameters: D6-3's "per-tenant model pinning with versioned
migration" means a model change is a schema-adjacent event (a new
``embedding_version``, a backfill), not something a caller picks
per-request.
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProviderError(Exception):
    """An embedding call failed. ``retryable`` distinguishes a quota/
    network blip — propagate uncaught so the queue's own retry-then-DLQ
    mechanism absorbs it, with issue #45's per-tenant fair queuing
    already preventing one throttled tenant from starving others (no
    separate backoff mechanism needed here) — from a genuinely permanent
    failure, which the pipeline handler catches and records against the
    document's failure_stage/failure_reason."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmbeddingProviderPort(Protocol):
    model: str
    embedding_version: int

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Returns one vector per input text, same order. Batches
        internally against the provider's own request-size limits —
        callers pass as many texts as they have pending; this port
        decides how many HTTP calls that takes."""
        ...

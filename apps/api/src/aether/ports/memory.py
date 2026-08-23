"""Memory Service ports (§3.2.6): rolling thread compaction, MVP tiers
(a) window and (b) summary. Tier (c) — long-term user/workspace memory,
opt-in/user-visible/erasable — is Phase 2, not implemented here.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aether.domain.entities import MemorySummary, Message, MessageRole

__all__ = [
    "MemoryCompactionPort",
    "MemorySummary",
    "MemorySummaryStorePort",
    "Message",
    "MessageRole",
]


class MemoryCompactionPort(Protocol):
    """Produces a new rolling summary from a previous one (if any) plus
    the messages that overflowed the window budget. Real if a provider
    key is configured (adapters/llm/memory_compaction.py); a real,
    honest deterministic digest otherwise
    (adapters/local/truncating_memory_compaction.py) — never a silent
    no-op, since dropping overflowed history entirely (rather than even
    a crude digest) would be a worse fallback than a real one is worth
    avoiding."""

    async def summarize(
        self, *, previous_summary: str | None, messages_to_compact: list[Message]
    ) -> str: ...


class MemorySummaryStorePort(Protocol):
    """Pool-bound (see ports.chat.MessageStorePort's docstring for why —
    SendMessage is a Container-level singleton with no per-request
    connection to share). "latest-wins per thread" (§8.1): ``upsert``
    replaces the whole row, it never appends."""

    async def get_by_thread(self, workspace_id: UUID, thread_id: UUID) -> MemorySummary | None: ...

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
    ) -> MemorySummary: ...

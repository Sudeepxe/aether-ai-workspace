"""Memory Service MVP tiers (§3.2.6 a+b): a token-budgeted recent-turn
window plus a rolling compaction summary for whatever falls out of that
budget. Plain owned code (ADR-6.1's posture, already established for
retrieval/chunking) — no orchestration framework for something this
mechanical.

Failure posture (§3.2.6, explicit in the blueprint): compaction lag or a
summary-model outage must never fail a turn. If the compactor call
raises, this falls back to window-only for that turn (the messages that
would have been compacted are simply not included this time) and keeps
the *previous* summary unchanged — never a half-written or corrupted
summary row.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
import tiktoken

from aether.domain.entities import Message
from aether.ports.chat import MessageStorePort
from aether.ports.memory import MemoryCompactionPort, MemorySummaryStorePort
from aether.ports.security import IdPort

log = structlog.get_logger(__name__)

_encoding = tiktoken.get_encoding("cl100k_base")

_COMPACTION_FETCH_LIMIT = 200
"""How far back MemoryAssembler looks for messages that might still need
compacting. Not unbounded: a single thread growing past this many
messages between compactions is an edge case outside MVP scope (the
degradation is graceful, not a correctness bug — see this module's
docstring on failure posture)."""
_WINDOW_TOKEN_BUDGET = 1200
"""§6's layered prompt-assembly example: "memory: summary + window (≤
1,200, evict oldest-first)"."""


def _count_tokens(text: str) -> int:
    return len(_encoding.encode(text))


@dataclass(frozen=True, slots=True)
class MemoryContext:
    summary: str | None
    window: list[Message]
    """Oldest-first, token-budgeted — always includes at least the most
    recent message, even if it alone exceeds the budget."""


class MemoryAssembler:
    def __init__(
        self,
        *,
        messages: MessageStorePort,
        summaries: MemorySummaryStorePort,
        compactor: MemoryCompactionPort,
        compactor_model_label: str,
        ids: IdPort,
    ) -> None:
        self._messages = messages
        self._summaries = summaries
        self._compactor = compactor
        self._compactor_model_label = compactor_model_label
        self._ids = ids

    async def assemble(self, workspace_id: UUID, thread_id: UUID) -> MemoryContext:
        existing = await self._summaries.get_by_thread(workspace_id, thread_id)
        fetched = await self._messages.recent(
            workspace_id, thread_id, limit=_COMPACTION_FETCH_LIMIT
        )
        candidates = [m for m in fetched if existing is None or m.seq > existing.upto_seq]

        window, to_compact = _split_by_budget(candidates)

        summary_text = existing.content if existing is not None else None
        if to_compact:
            try:
                summary_text = await self._compactor.summarize(
                    previous_summary=summary_text, messages_to_compact=to_compact
                )
                await self._summaries.upsert(
                    id=self._ids.new_id(),
                    workspace_id=workspace_id,
                    thread_id=thread_id,
                    upto_seq=to_compact[-1].seq,
                    content=summary_text,
                    model=self._compactor_model_label,
                    token_count=_count_tokens(summary_text),
                )
            except Exception as exc:
                # Never fail the turn — proceed window-only, previous
                # summary (if any) stays as it was.
                log.warning(
                    "memory_compaction_failed_proceeding_window_only",
                    thread_id=str(thread_id),
                    error=str(exc),
                )

        return MemoryContext(summary=summary_text, window=window)


def _split_by_budget(candidates: list[Message]) -> tuple[list[Message], list[Message]]:
    """Walks newest-to-oldest accumulating tokens until the budget would
    be exceeded; everything that fits is the window, everything older
    is what needs compacting. Always keeps at least the newest message
    in the window, even alone over budget."""
    window: list[Message] = []
    token_total = 0
    cutoff = len(candidates)
    for i in range(len(candidates) - 1, -1, -1):
        t = _count_tokens(candidates[i].content)
        if window and token_total + t > _WINDOW_TOKEN_BUDGET:
            cutoff = i + 1
            break
        window.insert(0, candidates[i])
        token_total += t
        cutoff = i
    return window, candidates[:cutoff]

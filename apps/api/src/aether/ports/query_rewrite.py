"""QueryRewritePort (§3.2.5, ADR-6.3): condenses a follow-up turn into
a standalone query using the thread's recent history — e.g. "what
about its pricing?" -> "What is Acme Corp's pricing?".

I/O-only, matching ChunkSearchPort's split (issue #56): timeout
handling, the first-turn no-op, and dual-feed assembly are pure logic
and live in app/retrieval/query_rewrite.py. An implementation is
expected to raise on failure — timeout/fallback is the caller's job.
"""

from __future__ import annotations

from typing import Protocol

from aether.domain.entities import Message, MessageRole

__all__ = ["Message", "MessageRole", "QueryRewritePort"]


class QueryRewritePort(Protocol):
    async def rewrite(self, *, history: list[Message], raw_query: str) -> str:
        """Returns a standalone, condensed query given the thread's
        recent history and the latest user turn."""
        ...

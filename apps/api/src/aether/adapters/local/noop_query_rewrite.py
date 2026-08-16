"""A real, honest no-op query rewriter — mirrors EchoGenerator's/
LocalHashEmbeddingAdapter's established role for dev/CI without a
configured LLM key (§3.2.4, issues #38/#47). Returns the raw query
unchanged, never a fake rewrite; app/retrieval/query_rewrite.py's
first-turn/no-history skip already covers the case where no rewrite is
needed at all, so this adapter's job is honestly declining rather than
guessing at a rewrite it has no real model to produce.
"""

from __future__ import annotations

from aether.ports.query_rewrite import Message


class NoOpQueryRewriteAdapter:
    async def rewrite(self, *, history: list[Message], raw_query: str) -> str:
        return raw_query

"""A real, honest non-LLM compaction fallback — mirrors EchoGenerator's/
LocalHashEmbeddingAdapter's/NoOpQueryRewriteAdapter's established role
for dev/CI without a configured provider key (§3.2.4). Unlike
NoOpQueryRewriteAdapter (which can honestly decline by returning the
input unchanged — a rewrite that never happened is indistinguishable
from a skipped one), dropping overflowed history entirely here would
be a *worse* fallback than attempting one: a crude extractive digest
(first N words per message) is real, deterministic, and strictly
better than silently losing that context. It never claims to be a real
summary — its own ``model`` label makes that explicit wherever a
MemorySummary row is read.
"""

from __future__ import annotations

from aether.ports.memory import Message, MessageRole

MODEL_NAME = "truncating-fallback"
_WORDS_PER_MESSAGE = 12
_ROLE_LABEL = {
    MessageRole.USER: "User",
    MessageRole.ASSISTANT: "Assistant",
    MessageRole.SYSTEM: "System",
}
_MAX_OUTPUT_CHARS = 2000
"""A hard cap so repeated compactions (each one replacing, not
appending to, the prior summary — "latest-wins per thread", §8.1) can
never grow unbounded even without real LLM compression. Trims from the
front (oldest content) when over budget — recency matters most for
continuity."""


class TruncatingMemoryCompactionAdapter:
    async def summarize(
        self, *, previous_summary: str | None, messages_to_compact: list[Message]
    ) -> str:
        new_lines = [
            f"{_ROLE_LABEL[m.role]}: {' '.join(m.content.split()[:_WORDS_PER_MESSAGE])}"
            for m in messages_to_compact
        ]
        combined = "\n".join(([previous_summary] if previous_summary else []) + new_lines)
        if len(combined) > _MAX_OUTPUT_CHARS:
            combined = combined[-_MAX_OUTPUT_CHARS:]
        return combined

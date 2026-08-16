"""Echo generator (issue #26): a placeholder for the real LLM Router
(S4), proving the streaming spine — persistence, SSE contract,
cross-replica resume/cancel — end-to-end before a real provider exists.

Echoes the user's own turn back, word by word, with a small delay between
words so tests (and a human, in a demo) can actually observe streaming
rather than seeing content flash in as one flush.

Grounding-aware since issue #58 (ADR-6.4's Gate 2): with no real LLM
key configured in this dev environment, this is the *only* generator
that can genuinely demonstrate the "answer only from context, say so
explicitly otherwise" protocol — a real provider's own prompt-following
is the eval suite's job (ADR-6.4 says as much), but this test double's
job is proving the protocol's *contract* is real and observable, not
faked. It never falls back to echoing ``user_content`` when ``context``
is given — that would silently defeat the whole point.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from aether.ports.chat import GenerationUsage, GeneratorChunk, Message, RetrievedContext

_WORD_DELAY_SECONDS = 0.02
MODEL_NAME = "echo-v1"
NOT_IN_KNOWLEDGE_BASE_REPLY = "I don't have information about that in the knowledge base."


class EchoGenerator:
    @property
    def primary_model(self) -> str:
        return MODEL_NAME

    async def generate(
        self,
        *,
        thread_history: list[Message],
        user_content: str,
        context: RetrievedContext | None = None,
    ) -> AsyncIterator[GeneratorChunk]:
        reply = _build_reply(context, user_content)
        words = reply.split(" ")
        for i, word in enumerate(words):
            delta = word if i == 0 else f" {word}"
            yield delta
            await asyncio.sleep(_WORD_DELAY_SECONDS)
        # Word count, not a real tokenizer — an honest estimate for a
        # placeholder that echoes input back, not real provider usage.
        # cost_microcents=0: echoing costs nothing, unlike a real provider.
        yield GenerationUsage(
            prompt_tokens=len(words),
            completion_tokens=len(words),
            cost_microcents=0,
            model=MODEL_NAME,
        )


def _build_reply(context: RetrievedContext | None, user_content: str) -> str:
    if context is None:
        return user_content
    if not context.chunks:
        return NOT_IN_KNOWLEDGE_BASE_REPLY
    return "Grounded on: " + " | ".join(
        f"{chunk.document_title} ({chunk.section_path}): {chunk.content}"
        for chunk in context.chunks
    )

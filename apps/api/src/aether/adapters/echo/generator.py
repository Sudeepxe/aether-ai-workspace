"""Echo generator (issue #26): a placeholder for the real LLM Router
(S4), proving the streaming spine — persistence, SSE contract,
cross-replica resume/cancel — end-to-end before a real provider exists.

Echoes the user's own turn back, word by word, with a small delay between
words so tests (and a human, in a demo) can actually observe streaming
rather than seeing content flash in as one flush.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from aether.ports.chat import GenerationUsage, GeneratorChunk, Message

_WORD_DELAY_SECONDS = 0.02
MODEL_NAME = "echo-v1"


class EchoGenerator:
    @property
    def primary_model(self) -> str:
        return MODEL_NAME

    async def generate(
        self, *, thread_history: list[Message], user_content: str
    ) -> AsyncIterator[GeneratorChunk]:
        words = user_content.split(" ")
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

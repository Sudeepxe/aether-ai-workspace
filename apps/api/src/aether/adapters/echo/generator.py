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

from aether.ports.chat import Message

_WORD_DELAY_SECONDS = 0.02


class EchoGenerator:
    async def generate(
        self, *, thread_history: list[Message], user_content: str
    ) -> AsyncIterator[str]:
        words = user_content.split(" ")
        for i, word in enumerate(words):
            delta = word if i == 0 else f" {word}"
            yield delta
            await asyncio.sleep(_WORD_DELAY_SECONDS)

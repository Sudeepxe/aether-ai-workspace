from __future__ import annotations

import asyncio

from aether.ports.query_rewrite import Message


class FakeQueryRewriter:
    def __init__(
        self,
        *,
        rewritten: str | None = None,
        delay_seconds: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self._rewritten = rewritten
        self._delay_seconds = delay_seconds
        self._error = error
        self.calls: list[tuple[list[Message], str]] = []

    async def rewrite(self, *, history: list[Message], raw_query: str) -> str:
        self.calls.append((history, raw_query))
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._error is not None:
            raise self._error
        return self._rewritten if self._rewritten is not None else raw_query

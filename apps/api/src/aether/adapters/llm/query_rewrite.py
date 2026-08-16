"""A cheap-model condensing query rewriter (§3.2.5, ADR-6.3) — reuses
the LLM Router's own ProviderAdapterPort (a plain provider call, not
routed through the fallback-chain/circuit-breaker machinery: a failed
or slow rewrite already falls back to the raw query one layer up in
app/retrieval/query_rewrite.py, so retries/fallback here would just
spend the 150ms budget retrying something the caller is about to give
up on anyway).
"""

from __future__ import annotations

from aether.ports.llm import CompletionRequest, LlmMessage, LlmMessageRole, ProviderAdapterPort
from aether.ports.query_rewrite import Message, MessageRole

_REWRITE_SYSTEM_PROMPT = (
    "Given the conversation history and a follow-up message, rewrite the "
    "follow-up into a single standalone question that captures its full "
    "intent without needing the history. Preserve exact names, IDs, and "
    "acronyms from the follow-up verbatim. Reply with ONLY the rewritten "
    "question, nothing else."
)
_MAX_REWRITE_TOKENS = 100


class LlmQueryRewriteAdapter:
    def __init__(self, *, provider: ProviderAdapterPort, model: str) -> None:
        self._provider = provider
        self._model = model

    async def rewrite(self, *, history: list[Message], raw_query: str) -> str:
        messages = [
            LlmMessage(role=LlmMessageRole.SYSTEM, content=_REWRITE_SYSTEM_PROMPT),
            *(LlmMessage(role=_to_llm_role(m.role), content=m.content) for m in history),
            LlmMessage(role=LlmMessageRole.USER, content=raw_query),
        ]
        request = CompletionRequest(
            messages=messages, model=self._model, max_tokens=_MAX_REWRITE_TOKENS
        )
        text = ""
        async for chunk in self._provider.stream_completion(request):
            if isinstance(chunk, str):
                text += chunk
        rewritten = text.strip()
        return rewritten or raw_query


def _to_llm_role(role: MessageRole) -> LlmMessageRole:
    return LlmMessageRole(role.value)

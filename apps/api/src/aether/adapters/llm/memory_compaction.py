"""A cheap-model rolling-summary compactor (§3.2.6) — reuses the LLM
Router's own ProviderAdapterPort, same plain-call posture as
adapters/llm/query_rewrite.py (not routed through the fallback-chain/
circuit-breaker machinery: a failed compaction already falls back to
window-only context one layer up in app/chat/memory_assembly.py, so
retrying here would just delay a turn that's about to proceed without
it anyway).
"""

from __future__ import annotations

from aether.ports.llm import CompletionRequest, LlmMessage, LlmMessageRole, ProviderAdapterPort
from aether.ports.memory import Message, MessageRole

_COMPACTION_SYSTEM_PROMPT = (
    "You maintain a rolling summary of an ongoing conversation. You will be "
    "given the previous summary (if any) and a batch of new messages. "
    "Produce an updated summary that preserves every fact, decision, and "
    "named entity from both the previous summary and the new messages, "
    "condensed as tightly as possible. Reply with ONLY the updated summary "
    "text, nothing else."
)
_MAX_SUMMARY_TOKENS = 400
_ROLE_LABEL = {
    MessageRole.USER: "User",
    MessageRole.ASSISTANT: "Assistant",
    MessageRole.SYSTEM: "System",
}


class LlmMemoryCompactionAdapter:
    def __init__(self, *, provider: ProviderAdapterPort, model: str) -> None:
        self._provider = provider
        self._model = model

    async def summarize(
        self, *, previous_summary: str | None, messages_to_compact: list[Message]
    ) -> str:
        digest = "\n".join(f"{_ROLE_LABEL[m.role]}: {m.content}" for m in messages_to_compact)
        user_content = (
            f"Previous summary:\n{previous_summary or '(none yet)'}\n\nNew messages:\n{digest}"
        )
        request = CompletionRequest(
            messages=[
                LlmMessage(role=LlmMessageRole.SYSTEM, content=_COMPACTION_SYSTEM_PROMPT),
                LlmMessage(role=LlmMessageRole.USER, content=user_content),
            ],
            model=self._model,
            max_tokens=_MAX_SUMMARY_TOKENS,
        )
        text = ""
        async for chunk in self._provider.stream_completion(request):
            if isinstance(chunk, str):
                text += chunk
        return text.strip()

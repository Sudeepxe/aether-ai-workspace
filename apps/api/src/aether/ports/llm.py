"""LLM Router ports (§3.2.4, ADR-3.5): one internal interface over N
provider adapters, with a capability registry the router's fallback-chain
and budget logic can resolve against.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class LlmMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: LlmMessageRole
    content: str


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    messages: list[LlmMessage]
    model: str
    max_tokens: int


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    provider: str
    model: str
    max_context_tokens: int
    supports_tools: bool
    supports_vision: bool
    cost_per_1k_prompt_microcents: int
    cost_per_1k_completion_microcents: int


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """The provider's own authoritative token counts (§3.2.14: settlement
    is "from actual provider usage", not a local estimate — the local
    ceiling estimate is only for the pre-request admission check)."""

    prompt_tokens: int
    completion_tokens: int


# A provider stream yields text deltas throughout, then exactly one
# ProviderUsage as its final item (or none, if the stream errors before
# completing) — a tagged union rather than a separate return channel,
# since an async generator can't yield *and* return a distinct value.
ProviderChunk = str | ProviderUsage


class ProviderError(Exception):
    """A provider call failed. ``retryable`` distinguishes "try the next
    provider in the fallback chain" (network blip, 429, 5xx) from "this
    request itself is broken" (400 invalid request) — the router only
    ever falls back on the former, and only before the first token."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProviderAdapterPort(Protocol):
    """One real provider (OpenAI, Anthropic, ...). The router composes N
    of these behind one fallback chain — never called directly by the
    orchestrator, which only ever sees GeneratorPort (ports.chat)."""

    name: str

    def capabilities(self) -> list[ProviderCapability]: ...

    def stream_completion(self, request: CompletionRequest) -> AsyncIterator[ProviderChunk]:
        """Yields text deltas, then exactly one ProviderUsage as the
        final item once the provider's own usage accounting arrives.
        Raises ProviderError on failure — the router decides
        retry/fallback, this adapter never does."""
        ...

"""Shared real-provider call helper for Phase 7's two live runs
(docs/REMEDIATION_PLAN.md) — free-tier rate limits mean a case that
gets a 429/5xx must be retried with backoff, never silently dropped
(explicit user constraint). Also handles a real, observed quirk of
openai/gpt-oss-20b specifically: it streams hidden chain-of-thought via
a separate ``delta.reasoning`` field (confirmed by inspecting the raw
SSE body directly, not assumed), which counts against ``max_tokens`` —
a real run can exhaust the whole budget on reasoning before emitting
any visible ``content``, sometimes without a closing usage frame ever
arriving at all. An empty reply at that point is inconclusive, not a
result, so callers should retry once at a larger budget rather than
report it as-is.
"""

from __future__ import annotations

import asyncio
import sys

from aether.adapters.groq.completion import GroqCompletionAdapter
from aether.ports.llm import CompletionRequest, ProviderError, ProviderUsage

MAX_RETRIES = 6
BASE_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 120.0


async def call_with_backoff(
    adapter: GroqCompletionAdapter, request: CompletionRequest
) -> tuple[str, ProviderUsage | None, str | None]:
    """Returns (text, usage, error). error is None on success; if every
    retry is exhausted, error carries the reason instead of raising —
    the caller reports it explicitly rather than the case silently
    vanishing from the results."""
    delay = BASE_BACKOFF_SECONDS
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            text = ""
            usage: ProviderUsage | None = None
            async for chunk in adapter.stream_completion(request):
                if isinstance(chunk, ProviderUsage):
                    usage = chunk
                else:
                    text += chunk
            return text, usage, None
        except ProviderError as exc:
            if not exc.retryable or attempt == MAX_RETRIES:
                return "", None, f"{type(exc).__name__}: {exc}"
            print(
                f"    rate-limited or transient error (attempt {attempt}/{MAX_RETRIES}), "
                f"backing off {delay:.0f}s: {exc}",
                file=sys.stderr,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SECONDS)
    return "", None, "exhausted retries"


async def call_with_backoff_and_reasoning_retry(
    adapter: GroqCompletionAdapter,
    request: CompletionRequest,
    *,
    followup_max_tokens: int,
) -> tuple[str, ProviderUsage | None, str | None, bool]:
    """call_with_backoff, plus: if the reply comes back empty (the
    reasoning-token-exhaustion quirk described in this module's
    docstring), retries once at ``followup_max_tokens``. Returns
    (text, usage, error, retried_at_larger_budget)."""
    text, usage, error = await call_with_backoff(adapter, request)
    if error is not None:
        return text, usage, error, False
    if text.strip():
        return text, usage, error, False

    print(
        f"    reply was empty at the configured token budget (usage={usage}) — retrying "
        f"once at {followup_max_tokens} tokens.",
        file=sys.stderr,
    )
    followup_request = CompletionRequest(
        messages=request.messages, model=request.model, max_tokens=followup_max_tokens
    )
    text, usage, error = await call_with_backoff(adapter, followup_request)
    return text, usage, error, True

"""The SSE streaming event grammar (Blueprint §4.4, normative): pure
dataclasses — no I/O, no wire-format knowledge (that's http/sse.py's job,
an inbound-adapter concern). The orchestrator (app layer) produces these;
the HTTP layer serializes them to `text/event-stream` frames and the
Redis adapter buffers their serialized form for Last-Event-ID resume.

Grammar: ``meta`` (first) -> ``token``* -> ``citation``* -> ``banner``? ->
``usage`` -> ``done{status}``. ``error`` may precede ``done``. Every event
shares one monotonic ``seq`` counter per generation, forming the SSE
``id:`` field as ``{generation_id}:{seq}``.

``citation`` events (issue #60, ADR-8.6) follow the token stream for a
grounded reply that cleared Gate 1 — one per retrieved chunk that fed the
answer, emitted after the last token (or the error event, on a mid-stream
failure) and before ``usage``. A Gate-1 refusal or an ungrounded reply
emits zero citation events — a valid, common subsequence of the grammar
above, not a gap.

``banner`` still isn't defined yet — nothing produces it until provider
degradation banners (a later sprint) exist. Its absence is likewise a
valid subsequence (zero-or-more/optional).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class GenerationStatus(StrEnum):
    """A ``done`` event's terminal status (§4.4) — distinct from, and a
    superset of, domain.entities.MessageStatus: 'error' can terminate a
    *stream* even when no message row exists yet to carry that status."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MetaStreamEvent:
    generation_id: UUID
    seq: int
    model: str
    grounded: bool


@dataclass(frozen=True, slots=True)
class TokenStreamEvent:
    generation_id: UUID
    seq: int
    delta: str


@dataclass(frozen=True, slots=True)
class CitationStreamEvent:
    generation_id: UUID
    seq: int
    citation_id: UUID
    chunk_id: UUID | None
    document_title: str
    section_path: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True, slots=True)
class UsageStreamEvent:
    generation_id: UUID
    seq: int
    prompt_tokens: int
    completion_tokens: int
    cost_microcents: int


@dataclass(frozen=True, slots=True)
class ErrorStreamEvent:
    generation_id: UUID
    seq: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DoneStreamEvent:
    generation_id: UUID
    seq: int
    status: GenerationStatus


StreamEvent = (
    MetaStreamEvent
    | TokenStreamEvent
    | CitationStreamEvent
    | UsageStreamEvent
    | ErrorStreamEvent
    | DoneStreamEvent
)

# One source of truth for "what does each event type's SSE `event:`
# name / `data:` payload look like" — shared by the orchestrator (which
# buffers this payload for Last-Event-ID resume) and http/sse.py (which
# formats the *live* frame from the same domain event), so a resumed
# stream and a live one are byte-identical for the same seq.


def event_type_name(event: StreamEvent) -> str:
    if isinstance(event, MetaStreamEvent):
        return "meta"
    if isinstance(event, TokenStreamEvent):
        return "token"
    if isinstance(event, CitationStreamEvent):
        return "citation"
    if isinstance(event, UsageStreamEvent):
        return "usage"
    if isinstance(event, ErrorStreamEvent):
        return "error"
    return "done"


def event_payload(event: StreamEvent) -> dict[str, Any]:
    if isinstance(event, MetaStreamEvent):
        return {
            "generation_id": str(event.generation_id),
            "model": event.model,
            "grounded": event.grounded,
        }
    if isinstance(event, TokenStreamEvent):
        return {"delta": event.delta}
    if isinstance(event, CitationStreamEvent):
        return {
            "citation_id": str(event.citation_id),
            "chunk_id": str(event.chunk_id) if event.chunk_id is not None else None,
            "document_title": event.document_title,
            "section_path": event.section_path,
            "page_start": event.page_start,
            "page_end": event.page_end,
        }
    if isinstance(event, UsageStreamEvent):
        return {
            "prompt_tokens": event.prompt_tokens,
            "completion_tokens": event.completion_tokens,
            "cost_microcents": event.cost_microcents,
        }
    if isinstance(event, ErrorStreamEvent):
        return {"code": event.code, "message": event.message}
    return {"status": event.status.value}

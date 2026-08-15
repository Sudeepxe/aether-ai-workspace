from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from aether.domain.entities import MessageRole, MessageStatus


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class UpdateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ThreadResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    created_by: UUID
    title: str | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ThreadListResponse(BaseModel):
    items: list[ThreadResponse]
    next_cursor: str | None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=32_000)
    client_message_id: str = Field(min_length=1, max_length=200)


class MessageResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    thread_id: UUID
    seq: int
    role: MessageRole
    content: str
    status: MessageStatus
    client_message_id: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_microcents: int | None
    grounded: bool
    created_at: datetime


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    next_cursor: str | None


class GenerationStatusResponse(BaseModel):
    generation_id: UUID
    last_seq: int
    latest_event_type: str
    is_done: bool

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UsageModelRollupResponse(BaseModel):
    model: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    cost_microcents: int


class UsageResponse(BaseModel):
    workspace_id: UUID
    period_start: datetime
    by_model: list[UsageModelRollupResponse]
    total_cost_microcents: int


class BudgetResponse(BaseModel):
    workspace_id: UUID
    monthly_limit_microcents: int
    soft_pct: int
    current_period_start: date
    settled_microcents: int
    updated_at: datetime


class UpdateBudgetRequest(BaseModel):
    monthly_limit_microcents: int = Field(ge=0)

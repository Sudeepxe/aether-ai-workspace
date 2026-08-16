"""Usage + budget routes (issue #39, FR-AD-2/3, §3.2.14).

GET /usage uses ``get_chat_authorization`` (brief membership check, no
held connection) since it only reads through the pool-bound
``UsageLedgerPort`` — same reasoning as the generation routes. GET/PUT
/budget use ``get_workspace_scope`` since ``BudgetRepositoryPort`` is
connection-bound, matching GetWorkspace/UpdateWorkspace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from aether.app.metering.get_budget import GetBudgetCommand
from aether.app.metering.get_usage import GetUsageCommand
from aether.app.metering.update_budget import UpdateBudgetCommand
from aether.domain.entities import Membership
from aether.domain.errors import BudgetConcurrencyConflictError
from aether.domain.policy import MANAGE_BUDGETS
from aether.http.authz import AuthRequirement, require_capability, route_auth
from aether.http.composition import Container, WorkspaceScope
from aether.http.deps import get_chat_authorization, get_container, get_workspace_scope
from aether.http.rate_limit_deps import RateLimitClass, rate_limit_by_user
from aether.http.schemas.metering import (
    BudgetResponse,
    UpdateBudgetRequest,
    UsageModelRollupResponse,
    UsageResponse,
)
from aether.ports.metering import Budget, UsageRollup

router = APIRouter(prefix="/v1", tags=["metering"])


def _current_month_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _to_usage_response(rollup: UsageRollup) -> UsageResponse:
    return UsageResponse(
        workspace_id=rollup.workspace_id,
        period_start=rollup.period_start,
        by_model=[
            UsageModelRollupResponse(
                model=m.model,
                request_count=m.request_count,
                prompt_tokens=m.prompt_tokens,
                completion_tokens=m.completion_tokens,
                cost_microcents=m.cost_microcents,
            )
            for m in rollup.by_model
        ],
        total_cost_microcents=rollup.total_cost_microcents,
    )


def _to_budget_response(budget: Budget) -> BudgetResponse:
    return BudgetResponse(
        workspace_id=budget.workspace_id,
        monthly_limit_microcents=budget.monthly_limit_microcents,
        soft_pct=budget.soft_pct,
        current_period_start=budget.current_period_start,
        settled_microcents=budget.settled_microcents,
        updated_at=budget.updated_at,
    )


def _etag(budget: Budget) -> str:
    return budget.updated_at.isoformat()


@router.get(
    "/workspaces/{workspace_id}/usage",
    response_model=UsageResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_usage(
    workspace_id: UUID,
    container: Container = Depends(get_container),
    _caller_membership: Membership = Depends(get_chat_authorization),
    since: datetime | None = Query(default=None),
) -> UsageResponse:
    rollup = await container.get_usage.execute(
        GetUsageCommand(workspace_id=workspace_id, since=since or _current_month_start())
    )
    return _to_usage_response(rollup)


@router.get(
    "/workspaces/{workspace_id}/budget",
    response_model=BudgetResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_budget(
    workspace_id: UUID,
    response: Response,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> BudgetResponse:
    budget = await scope.get_budget.execute(GetBudgetCommand(workspace_id=workspace_id))
    response.headers["ETag"] = _etag(budget)
    return _to_budget_response(budget)


@router.put(
    "/workspaces/{workspace_id}/budget",
    response_model=BudgetResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def update_budget(
    workspace_id: UUID,
    body: UpdateBudgetRequest,
    response: Response,
    scope: WorkspaceScope = Depends(get_workspace_scope),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> BudgetResponse:
    require_capability(scope.caller_membership.role, MANAGE_BUDGETS)
    if if_match is None:
        raise BudgetConcurrencyConflictError("If-Match header is required for PUT")
    expected_updated_at = datetime.fromisoformat(if_match)
    if expected_updated_at.tzinfo is None:
        expected_updated_at = expected_updated_at.replace(tzinfo=UTC)

    budget = await scope.update_budget.execute(
        UpdateBudgetCommand(
            workspace_id=workspace_id,
            actor_user_id=scope.caller_membership.user_id,
            monthly_limit_microcents=body.monthly_limit_microcents,
            expected_updated_at=expected_updated_at,
        )
    )
    response.headers["ETag"] = _etag(budget)
    return _to_budget_response(budget)

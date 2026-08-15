"""Generation routes (§4.3): cancel and status. Both use
``get_chat_authorization`` (brief membership check, connection released
immediately), not ``get_workspace_scope`` — neither route does any other
DB work, so holding a connection open for either would be pure waste.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from aether.app.chat.cancel_generation import CancelGenerationCommand
from aether.app.chat.get_generation_status import GetGenerationStatusCommand
from aether.domain.entities import Membership
from aether.domain.policy import CREATE_MESSAGES
from aether.http.authz import AuthRequirement, require_capability, route_auth
from aether.http.composition import Container
from aether.http.deps import get_chat_authorization, get_container
from aether.http.rate_limit_deps import RateLimitClass, rate_limit_by_user
from aether.http.schemas.threads import GenerationStatusResponse

router = APIRouter(prefix="/v1", tags=["generations"])


@router.delete(
    "/workspaces/{workspace_id}/generations/{generation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def cancel_generation(
    workspace_id: UUID,
    generation_id: UUID,
    container: Container = Depends(get_container),
    caller_membership: Membership = Depends(get_chat_authorization),
) -> None:
    require_capability(caller_membership.role, CREATE_MESSAGES)
    await container.cancel_generation.execute(
        CancelGenerationCommand(workspace_id=workspace_id, generation_id=generation_id)
    )


@router.get(
    "/workspaces/{workspace_id}/generations/{generation_id}",
    response_model=GenerationStatusResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_generation_status(
    workspace_id: UUID,
    generation_id: UUID,
    container: Container = Depends(get_container),
    _caller_membership: Membership = Depends(get_chat_authorization),
) -> GenerationStatusResponse:
    view = await container.get_generation_status.execute(
        GetGenerationStatusCommand(workspace_id=workspace_id, generation_id=generation_id)
    )
    return GenerationStatusResponse(
        generation_id=view.generation_id,
        last_seq=view.last_seq,
        latest_event_type=view.latest_event_type,
        is_done=view.is_done,
    )

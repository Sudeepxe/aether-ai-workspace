"""GET /v1/me — the minimal authenticated identity endpoint (approved as
Sprint 1's auth proof-of-concept). Deliberately returns only the caller's
own identity, no workspace list — no workspace CRUD exists yet (S2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aether.domain.errors import UserNotFoundError
from aether.http.authz import AuthRequirement, route_auth
from aether.http.composition import Container
from aether.http.deps import AuthenticatedSession, get_container, get_current_session
from aether.http.schemas.auth import UserResponse

router = APIRouter(prefix="/v1", tags=["me"])


@router.get(
    "/me", response_model=UserResponse, openapi_extra=route_auth(AuthRequirement.AUTHENTICATED)
)
async def get_me(
    container: Container = Depends(get_container),
    session: AuthenticatedSession = Depends(get_current_session),
) -> UserResponse:
    user = await container.users.get_by_id(session.user_id)
    if user is None:  # pragma: no cover — token valid but user since deleted; defensive only
        raise UserNotFoundError(str(session.user_id))
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name)

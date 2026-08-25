"""API key routes (FR-API-2, §4.3). Create/list/revoke — Admin+ only
(§7.3's "Budgets, model policy, API keys" row), the same RateLimitClass.AUTH
class create_invitation uses (§4.6's OWASP API6 mapping explicitly names
key-creation abuse as a sensitive business flow)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from aether.app.api_keys.create_api_key import CreateApiKeyCommand
from aether.app.api_keys.list_api_keys import ListApiKeysCommand
from aether.app.api_keys.revoke_api_key import RevokeApiKeyCommand
from aether.domain.entities import ApiKey
from aether.domain.policy import MANAGE_API_KEYS
from aether.http.authz import AuthRequirement, require_capability, route_auth
from aether.http.composition import WorkspaceScope
from aether.http.deps import get_workspace_scope
from aether.http.rate_limit_deps import RateLimitClass, rate_limit_by_user
from aether.http.schemas.api_keys import (
    ApiKeyListResponse,
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
)

router = APIRouter(prefix="/v1", tags=["api-keys"])


def _to_api_key_response(api_key: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=api_key.id,
        workspace_id=api_key.workspace_id,
        prefix=api_key.prefix,
        name=api_key.name,
        scopes=sorted(api_key.scopes),
        expires_at=api_key.expires_at,
        revoked_at=api_key.revoked_at,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
    )


@router.post(
    "/workspaces/{workspace_id}/api-keys",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.AUTH))],
)
async def create_api_key(
    workspace_id: UUID,
    body: CreateApiKeyRequest,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> CreateApiKeyResponse:
    require_capability(scope.caller_membership.role, MANAGE_API_KEYS)
    result = await scope.create_api_key.execute(
        CreateApiKeyCommand(
            workspace_id=workspace_id,
            name=body.name,
            scopes=frozenset(body.scopes),
            created_by=scope.caller_membership.user_id,
            expires_at=body.expires_at,
        )
    )
    return CreateApiKeyResponse(
        id=result.api_key.id,
        workspace_id=result.api_key.workspace_id,
        prefix=result.api_key.prefix,
        name=result.api_key.name,
        scopes=sorted(result.api_key.scopes),
        expires_at=result.api_key.expires_at,
        created_at=result.api_key.created_at,
        raw_key=result.raw_key,
    )


@router.get(
    "/workspaces/{workspace_id}/api-keys",
    response_model=ApiKeyListResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def list_api_keys(
    workspace_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> ApiKeyListResponse:
    require_capability(scope.caller_membership.role, MANAGE_API_KEYS)
    keys = await scope.list_api_keys.execute(ListApiKeysCommand(workspace_id=workspace_id))
    return ApiKeyListResponse(items=[_to_api_key_response(k) for k in keys])


@router.delete(
    "/workspaces/{workspace_id}/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.AUTH))],
)
async def revoke_api_key(
    workspace_id: UUID,
    key_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> None:
    require_capability(scope.caller_membership.role, MANAGE_API_KEYS)
    await scope.revoke_api_key.execute(
        RevokeApiKeyCommand(
            workspace_id=workspace_id,
            key_id=key_id,
            actor_user_id=scope.caller_membership.user_id,
        )
    )

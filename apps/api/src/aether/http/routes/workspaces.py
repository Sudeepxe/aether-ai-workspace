"""Workspace + membership routes (FR-ID-2, FR-ID-4, §4.3).

Role gating is two-step, matching http/authz.py's design: WORKSPACE_MEMBER
(declared, boot-checked) proves the caller is *a* member — resolved by
get_workspace_scope before the route body runs; require_capability()
(called explicitly in the routes that need more than "any member") proves
they're the *right* member. GET routes need only the first; mutations
need both.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status

from aether.app.workspaces.create_workspace import CreateWorkspace, CreateWorkspaceCommand
from aether.app.workspaces.delete_workspace import DeleteWorkspaceCommand
from aether.app.workspaces.get_deletion_job import GetDeletionJobCommand
from aether.app.workspaces.get_export_job import ExportJobView, GetExportJobCommand
from aether.app.workspaces.get_workspace import GetWorkspaceCommand
from aether.app.workspaces.manage_members import (
    ListMembersCommand,
    RemoveMemberCommand,
    UpdateMemberRoleCommand,
)
from aether.app.workspaces.request_export import RequestWorkspaceExportCommand
from aether.app.workspaces.update_workspace import UpdateWorkspaceCommand
from aether.domain.entities import DeletionJob, Membership, Workspace
from aether.domain.errors import WorkspaceConcurrencyConflictError
from aether.domain.policy import MANAGE_BUDGETS, MANAGE_MEMBERS, MANAGE_WORKSPACE
from aether.http.authz import AuthRequirement, require_capability, route_auth
from aether.http.composition import WorkspaceScope
from aether.http.deps import (
    AuthenticatedSession,
    get_current_session,
    get_new_workspace_connection,
    get_workspace_scope,
)
from aether.http.rate_limit_deps import RateLimitClass, rate_limit_by_user
from aether.http.schemas.workspaces import (
    CreateWorkspaceRequest,
    DeletionJobResponse,
    ExportJobResponse,
    MembershipResponse,
    UpdateMemberRoleRequest,
    UpdateWorkspaceRequest,
    WorkspaceResponse,
)

router = APIRouter(prefix="/v1", tags=["workspaces"])


def _etag(workspace: Workspace) -> str:
    return workspace.updated_at.isoformat()


def _to_workspace_response(workspace: Workspace) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        settings=workspace.settings,
        model_policy=workspace.model_policy,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _to_deletion_job_response(job: DeletionJob) -> DeletionJobResponse:
    return DeletionJobResponse(
        id=job.id,
        workspace_id=job.workspace_id,
        requested_by=job.requested_by,
        status=job.status,
        evidence=job.evidence,
        failure_reason=job.failure_reason,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _to_export_job_response(view: ExportJobView) -> ExportJobResponse:
    job = view.job
    return ExportJobResponse(
        id=job.id,
        workspace_id=job.workspace_id,
        requested_by=job.requested_by,
        status=job.status,
        evidence=job.evidence,
        failure_reason=job.failure_reason,
        download_url=view.download_url,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _to_membership_response(membership: Membership) -> MembershipResponse:
    return MembershipResponse(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=route_auth(AuthRequirement.AUTHENTICATED),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def create_workspace(
    body: CreateWorkspaceRequest,
    response: Response,
    session: AuthenticatedSession = Depends(get_current_session),
    new_workspace: tuple[UUID, CreateWorkspace] = Depends(get_new_workspace_connection),
) -> WorkspaceResponse:
    workspace_id, use_case = new_workspace
    workspace = await use_case.execute(
        CreateWorkspaceCommand(
            workspace_id=workspace_id, name=body.name, owner_user_id=session.user_id
        )
    )
    response.headers["ETag"] = _etag(workspace)
    return _to_workspace_response(workspace)


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_workspace(
    workspace_id: UUID,
    response: Response,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> WorkspaceResponse:
    workspace = await scope.get_workspace.execute(GetWorkspaceCommand(workspace_id=workspace_id))
    response.headers["ETag"] = _etag(workspace)
    return _to_workspace_response(workspace)


@router.patch(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def update_workspace(
    workspace_id: UUID,
    body: UpdateWorkspaceRequest,
    response: Response,
    scope: WorkspaceScope = Depends(get_workspace_scope),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> WorkspaceResponse:
    require_capability(scope.caller_membership.role, MANAGE_BUDGETS)
    if if_match is None:
        raise WorkspaceConcurrencyConflictError("If-Match header is required for PATCH")
    expected_updated_at = datetime.fromisoformat(if_match)
    if expected_updated_at.tzinfo is None:
        expected_updated_at = expected_updated_at.replace(tzinfo=UTC)

    workspace = await scope.update_workspace.execute(
        UpdateWorkspaceCommand(
            workspace_id=workspace_id,
            actor_user_id=scope.caller_membership.user_id,
            name=body.name,
            settings=body.settings,
            model_policy=body.model_policy,
            expected_updated_at=expected_updated_at,
        )
    )
    response.headers["ETag"] = _etag(workspace)
    return _to_workspace_response(workspace)


@router.delete(
    "/workspaces/{workspace_id}",
    response_model=DeletionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def delete_workspace(
    workspace_id: UUID,
    response: Response,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> DeletionJobResponse:
    require_capability(scope.caller_membership.role, MANAGE_WORKSPACE)
    job = await scope.delete_workspace.execute(
        DeleteWorkspaceCommand(
            workspace_id=workspace_id, actor_user_id=scope.caller_membership.user_id
        )
    )
    response.headers["Location"] = f"/v1/workspaces/{workspace_id}/deletion-jobs/{job.id}"
    return _to_deletion_job_response(job)


@router.get(
    "/workspaces/{workspace_id}/deletion-jobs/{job_id}",
    response_model=DeletionJobResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_deletion_job(
    workspace_id: UUID, job_id: UUID, scope: WorkspaceScope = Depends(get_workspace_scope)
) -> DeletionJobResponse:
    job = await scope.get_deletion_job.execute(
        GetDeletionJobCommand(workspace_id=workspace_id, job_id=job_id)
    )
    return _to_deletion_job_response(job)


@router.post(
    "/workspaces/{workspace_id}:export",
    response_model=ExportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def export_workspace(
    workspace_id: UUID,
    response: Response,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> ExportJobResponse:
    # §7.3's RBAC matrix gates "Workspace delete/export/transfer" as a
    # single Owner-only row — the same capability DELETE uses, not a
    # looser admin tier (some issue text elsewhere calls this "Admin-
    # only" loosely; the blueprint's matrix is the authoritative source
    # policy.py is transcribed from).
    require_capability(scope.caller_membership.role, MANAGE_WORKSPACE)
    job = await scope.request_export.execute(
        RequestWorkspaceExportCommand(
            workspace_id=workspace_id, actor_user_id=scope.caller_membership.user_id
        )
    )
    response.headers["Location"] = f"/v1/workspaces/{workspace_id}/export-jobs/{job.id}"
    return _to_export_job_response(ExportJobView(job=job, download_url=None))


@router.get(
    "/workspaces/{workspace_id}/export-jobs/{job_id}",
    response_model=ExportJobResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def get_export_job(
    workspace_id: UUID, job_id: UUID, scope: WorkspaceScope = Depends(get_workspace_scope)
) -> ExportJobResponse:
    view = await scope.get_export_job.execute(
        GetExportJobCommand(workspace_id=workspace_id, job_id=job_id)
    )
    return _to_export_job_response(view)


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=list[MembershipResponse],
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def list_members(
    workspace_id: UUID, scope: WorkspaceScope = Depends(get_workspace_scope)
) -> list[MembershipResponse]:
    members = await scope.list_members.execute(ListMembersCommand(workspace_id=workspace_id))
    return [_to_membership_response(m) for m in members]


@router.patch(
    "/workspaces/{workspace_id}/members/{user_id}",
    response_model=MembershipResponse,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def update_member_role(
    workspace_id: UUID,
    user_id: UUID,
    body: UpdateMemberRoleRequest,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> MembershipResponse:
    require_capability(scope.caller_membership.role, MANAGE_MEMBERS)
    membership = await scope.update_member_role.execute(
        UpdateMemberRoleCommand(
            workspace_id=workspace_id,
            target_user_id=user_id,
            new_role=body.role,
            actor_user_id=scope.caller_membership.user_id,
        )
    )
    return _to_membership_response(membership)


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
    dependencies=[Depends(rate_limit_by_user(RateLimitClass.CHEAP))],
)
async def remove_member(
    workspace_id: UUID, user_id: UUID, scope: WorkspaceScope = Depends(get_workspace_scope)
) -> None:
    require_capability(scope.caller_membership.role, MANAGE_MEMBERS)
    await scope.remove_member.execute(
        RemoveMemberCommand(
            workspace_id=workspace_id,
            target_user_id=user_id,
            actor_user_id=scope.caller_membership.user_id,
        )
    )

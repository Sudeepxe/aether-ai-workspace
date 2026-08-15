"""Invitation routes (FR-ID-3, §4.3)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from aether.app.invitations.accept_invitation import AcceptInvitation, AcceptInvitationCommand
from aether.app.invitations.create_invitation import CreateInvitationCommand
from aether.app.invitations.revoke_invitation import RevokeInvitationCommand
from aether.domain.entities import Invitation, Membership
from aether.domain.policy import MANAGE_MEMBERS
from aether.http.authz import AuthRequirement, require_capability, route_auth
from aether.http.composition import WorkspaceScope
from aether.http.deps import (
    AuthenticatedSession,
    get_current_session,
    get_invitation_acceptance_scope,
    get_workspace_scope,
)
from aether.http.schemas.workspaces import (
    CreateInvitationRequest,
    InvitationResponse,
    MembershipResponse,
)

router = APIRouter(prefix="/v1", tags=["invitations"])


def _to_invitation_response(invitation: Invitation) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        workspace_id=invitation.workspace_id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
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
    "/workspaces/{workspace_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
)
async def create_invitation(
    workspace_id: UUID,
    body: CreateInvitationRequest,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> InvitationResponse:
    require_capability(scope.caller_membership.role, MANAGE_MEMBERS)
    result = await scope.create_invitation.execute(
        CreateInvitationCommand(
            workspace_id=workspace_id,
            email=body.email,
            role=body.role,
            invited_by=scope.caller_membership.user_id,
        )
    )
    # raw_token is intentionally never returned here — it's sent by email
    # (once EmailPort lands) and never crosses the trust boundary as a
    # readable API response; only its hash is ever persisted.
    return _to_invitation_response(result.invitation)


@router.delete(
    "/workspaces/{workspace_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=route_auth(AuthRequirement.WORKSPACE_MEMBER),
)
async def revoke_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    scope: WorkspaceScope = Depends(get_workspace_scope),
) -> None:
    require_capability(scope.caller_membership.role, MANAGE_MEMBERS)
    await scope.revoke_invitation.execute(
        RevokeInvitationCommand(
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            actor_user_id=scope.caller_membership.user_id,
        )
    )


@router.post(
    "/invitations/{token}:accept",
    response_model=MembershipResponse,
    openapi_extra=route_auth(AuthRequirement.AUTHENTICATED),
)
async def accept_invitation(
    token: str,
    session: AuthenticatedSession = Depends(get_current_session),
    use_case: AcceptInvitation = Depends(get_invitation_acceptance_scope),
) -> MembershipResponse:
    membership = await use_case.execute(
        AcceptInvitationCommand(raw_token=token, accepting_user_id=session.user_id)
    )
    return _to_membership_response(membership)

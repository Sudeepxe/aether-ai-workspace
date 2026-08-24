from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from aether.domain.entities import (
    AuditEvent,
    DeletionJob,
    DeletionJobStatus,
    ExportJob,
    ExportJobStatus,
    Invitation,
    Membership,
    MembershipRole,
    Workspace,
)
from aether.ports.metering import Budget


@dataclass
class RecordedAuditEvent:
    id: UUID
    workspace_id: UUID | None
    actor_user_id: UUID | None
    actor_key_id: UUID | None
    action: str
    target_type: str
    target_id: UUID
    metadata: dict[str, Any]


class FakeAuditLog:
    def __init__(self) -> None:
        self.recorded: list[RecordedAuditEvent] = []

    async def record(
        self,
        *,
        id: UUID,
        workspace_id: UUID | None,
        actor_user_id: UUID | None,
        actor_key_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID,
        metadata: dict[str, Any],
    ) -> None:
        self.recorded.append(
            RecordedAuditEvent(
                id=id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_key_id=actor_key_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
            )
        )

    async def list_by_workspace(
        self, workspace_id: UUID, *, cursor: tuple[datetime, UUID] | None, limit: int
    ) -> list[AuditEvent]:
        raise NotImplementedError  # unused by any unit-tested use case yet


class FakeWorkspaceRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, Workspace] = {}

    async def create(
        self,
        *,
        id: UUID,
        name: str,
        slug: str,
        settings: dict[str, Any],
        model_policy: dict[str, Any],
    ) -> Workspace:
        now = datetime.now().astimezone()
        workspace = Workspace(
            id=id,
            name=name,
            slug=slug,
            settings=settings,
            model_policy=model_policy,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._rows[id] = workspace
        return workspace

    async def get_by_id(self, workspace_id: UUID) -> Workspace | None:
        workspace = self._rows.get(workspace_id)
        if workspace is None or workspace.deleted_at is not None:
            return None
        return workspace

    async def update(
        self,
        workspace_id: UUID,
        *,
        name: str,
        settings: dict[str, Any],
        model_policy: dict[str, Any],
        expected_updated_at: datetime,
    ) -> Workspace | None:
        current = self._rows.get(workspace_id)
        if (
            current is None
            or current.deleted_at is not None
            or current.updated_at != expected_updated_at
        ):
            return None
        updated = Workspace(
            id=current.id,
            name=name,
            slug=current.slug,
            settings=settings,
            model_policy=model_policy,
            created_at=current.created_at,
            updated_at=datetime.now().astimezone(),
            deleted_at=None,
        )
        self._rows[workspace_id] = updated
        return updated

    async def soft_delete(self, workspace_id: UUID, *, deleted_at: datetime) -> None:
        current = self._rows.get(workspace_id)
        if current is not None:
            self._rows[workspace_id] = Workspace(
                id=current.id,
                name=current.name,
                slug=current.slug,
                settings=current.settings,
                model_policy=current.model_policy,
                created_at=current.created_at,
                updated_at=current.updated_at,
                deleted_at=deleted_at,
            )


class FakeDeletionJobRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, DeletionJob] = {}

    async def create(self, *, id: UUID, workspace_id: UUID, requested_by: UUID) -> DeletionJob:
        now = datetime.now().astimezone()
        job = DeletionJob(
            id=id,
            workspace_id=workspace_id,
            requested_by=requested_by,
            status=DeletionJobStatus.QUEUED,
            evidence={},
            failure_reason=None,
            created_at=now,
            updated_at=now,
            completed_at=None,
            verified_at=None,
            verification_passed=None,
        )
        self._rows[id] = job
        return job

    async def get_by_id(self, workspace_id: UUID, job_id: UUID) -> DeletionJob | None:
        job = self._rows.get(job_id)
        return job if job is not None and job.workspace_id == workspace_id else None


class FakeExportJobRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, ExportJob] = {}

    async def create(self, *, id: UUID, workspace_id: UUID, requested_by: UUID) -> ExportJob:
        now = datetime.now().astimezone()
        job = ExportJob(
            id=id,
            workspace_id=workspace_id,
            requested_by=requested_by,
            status=ExportJobStatus.QUEUED,
            archive_object_key=None,
            evidence={},
            failure_reason=None,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        self._rows[id] = job
        return job

    async def get_by_id(self, workspace_id: UUID, job_id: UUID) -> ExportJob | None:
        job = self._rows.get(job_id)
        return job if job is not None and job.workspace_id == workspace_id else None

    def seed(self, job: ExportJob) -> None:
        """Test-only: directly install a job row, e.g. to simulate one
        the worker-plane saga already completed."""
        self._rows[job.id] = job


class FakeMembershipRepository:
    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, UUID], Membership] = {}

    async def create(
        self, *, id: UUID, workspace_id: UUID, user_id: UUID, role: MembershipRole
    ) -> Membership:
        now = datetime.now().astimezone()
        membership = Membership(
            id=id,
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            created_at=now,
            updated_at=now,
        )
        self._rows[(workspace_id, user_id)] = membership
        return membership

    async def get(self, workspace_id: UUID, user_id: UUID) -> Membership | None:
        return self._rows.get((workspace_id, user_id))

    async def list_by_workspace(self, workspace_id: UUID) -> list[Membership]:
        return [m for (ws, _), m in self._rows.items() if ws == workspace_id]

    async def update_role(
        self, workspace_id: UUID, user_id: UUID, *, role: MembershipRole
    ) -> Membership | None:
        current = self._rows.get((workspace_id, user_id))
        if current is None:
            return None
        updated = Membership(
            id=current.id,
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            created_at=current.created_at,
            updated_at=datetime.now().astimezone(),
        )
        self._rows[(workspace_id, user_id)] = updated
        return updated

    async def delete(self, workspace_id: UUID, user_id: UUID) -> None:
        self._rows.pop((workspace_id, user_id), None)

    async def count_by_role(self, workspace_id: UUID, role: MembershipRole) -> int:
        return sum(1 for (ws, _), m in self._rows.items() if ws == workspace_id and m.role == role)


class FakeBudgetRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, Budget] = {}

    async def get(self, workspace_id: UUID) -> Budget | None:
        return self._rows.get(workspace_id)

    async def create(
        self,
        *,
        workspace_id: UUID,
        monthly_limit_microcents: int,
        soft_pct: int,
        current_period_start: date,
    ) -> Budget:
        budget = Budget(
            workspace_id=workspace_id,
            monthly_limit_microcents=monthly_limit_microcents,
            soft_pct=soft_pct,
            current_period_start=current_period_start,
            settled_microcents=0,
            updated_at=datetime.now().astimezone(),
        )
        self._rows[workspace_id] = budget
        return budget

    async def update_limit(
        self,
        workspace_id: UUID,
        *,
        monthly_limit_microcents: int,
        expected_updated_at: datetime,
    ) -> Budget | None:
        current = self._rows.get(workspace_id)
        if current is None or current.updated_at != expected_updated_at:
            return None
        updated = Budget(
            workspace_id=current.workspace_id,
            monthly_limit_microcents=monthly_limit_microcents,
            soft_pct=current.soft_pct,
            current_period_start=current.current_period_start,
            settled_microcents=current.settled_microcents,
            updated_at=datetime.now().astimezone(),
        )
        self._rows[workspace_id] = updated
        return updated


class FakeInvitationRepository:
    def __init__(self) -> None:
        self._rows: dict[UUID, Invitation] = {}

    async def create(
        self,
        *,
        id: UUID,
        workspace_id: UUID,
        email: str,
        role: MembershipRole,
        token_hash: str,
        invited_by: UUID,
        expires_at: datetime,
    ) -> Invitation:
        now = datetime.now().astimezone()
        invitation = Invitation(
            id=id,
            workspace_id=workspace_id,
            email=email,
            role=role,
            token_hash=token_hash,
            invited_by=invited_by,
            expires_at=expires_at,
            consumed_at=None,
            created_at=now,
        )
        self._rows[id] = invitation
        return invitation

    async def get_by_token_hash(self, token_hash: str) -> Invitation | None:
        for invitation in self._rows.values():
            if invitation.token_hash == token_hash:
                return invitation
        return None

    async def consume(self, invitation_id: UUID, *, consumed_at: datetime) -> None:
        current = self._rows.get(invitation_id)
        if current is not None:
            self._rows[invitation_id] = Invitation(
                id=current.id,
                workspace_id=current.workspace_id,
                email=current.email,
                role=current.role,
                token_hash=current.token_hash,
                invited_by=current.invited_by,
                expires_at=current.expires_at,
                consumed_at=consumed_at,
                created_at=current.created_at,
            )

    async def delete(self, workspace_id: UUID, invitation_id: UUID) -> None:
        current = self._rows.get(invitation_id)
        if current is not None and current.workspace_id == workspace_id:
            del self._rows[invitation_id]

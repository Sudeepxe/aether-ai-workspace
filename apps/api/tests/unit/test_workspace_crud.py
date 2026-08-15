from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aether.app.workspaces.create_workspace import CreateWorkspace, CreateWorkspaceCommand
from aether.app.workspaces.delete_workspace import DeleteWorkspace, DeleteWorkspaceCommand
from aether.app.workspaces.get_workspace import GetWorkspace, GetWorkspaceCommand
from aether.app.workspaces.update_workspace import UpdateWorkspace, UpdateWorkspaceCommand
from aether.domain.entities import MembershipRole
from aether.domain.errors import WorkspaceConcurrencyConflictError, WorkspaceNotFoundError
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.workspaces import (
    FakeAuditLog,
    FakeMembershipRepository,
    FakeWorkspaceRepository,
)

pytestmark = pytest.mark.unit


async def test_create_workspace_makes_creator_the_sole_owner() -> None:
    workspaces = FakeWorkspaceRepository()
    memberships = FakeMembershipRepository()
    audit_log = FakeAuditLog()
    ids = FakeIdGenerator()
    use_case = CreateWorkspace(
        workspaces=workspaces, memberships=memberships, audit_log=audit_log, ids=ids
    )
    owner_id = UUID(int=999)
    workspace_id = UUID(int=1)

    result = await use_case.execute(
        CreateWorkspaceCommand(workspace_id=workspace_id, name="Acme", owner_user_id=owner_id)
    )

    assert result.name == "Acme"
    assert result.slug.startswith("acme-")
    membership = await memberships.get(workspace_id, owner_id)
    assert membership is not None
    assert membership.role == MembershipRole.OWNER
    assert audit_log.recorded[0].action == "workspace.created"


async def test_get_workspace_raises_not_found_for_unknown_id() -> None:
    use_case = GetWorkspace(workspaces=FakeWorkspaceRepository())
    with pytest.raises(WorkspaceNotFoundError):
        await use_case.execute(GetWorkspaceCommand(workspace_id=UUID(int=1)))


async def test_get_workspace_raises_not_found_for_soft_deleted_workspace() -> None:
    workspaces = FakeWorkspaceRepository()
    workspace = await workspaces.create(
        id=UUID(int=1), name="Acme", slug="acme", settings={}, model_policy={}
    )
    await workspaces.soft_delete(workspace.id, deleted_at=datetime.now(UTC))

    use_case = GetWorkspace(workspaces=workspaces)
    with pytest.raises(WorkspaceNotFoundError):
        await use_case.execute(GetWorkspaceCommand(workspace_id=workspace.id))


async def test_update_workspace_succeeds_with_matching_etag() -> None:
    workspaces = FakeWorkspaceRepository()
    audit_log = FakeAuditLog()
    workspace = await workspaces.create(
        id=UUID(int=1), name="Acme", slug="acme", settings={}, model_policy={}
    )
    use_case = UpdateWorkspace(workspaces=workspaces, audit_log=audit_log, ids=FakeIdGenerator())

    updated = await use_case.execute(
        UpdateWorkspaceCommand(
            workspace_id=workspace.id,
            actor_user_id=UUID(int=999),
            name="Acme Corp",
            settings={"theme": "dark"},
            model_policy={},
            expected_updated_at=workspace.updated_at,
        )
    )

    assert updated.name == "Acme Corp"
    assert updated.settings == {"theme": "dark"}
    assert audit_log.recorded[0].action == "workspace.updated"


async def test_update_workspace_raises_conflict_on_stale_etag() -> None:
    workspaces = FakeWorkspaceRepository()
    workspace = await workspaces.create(
        id=UUID(int=1), name="Acme", slug="acme", settings={}, model_policy={}
    )
    use_case = UpdateWorkspace(
        workspaces=workspaces, audit_log=FakeAuditLog(), ids=FakeIdGenerator()
    )
    stale = workspace.updated_at.replace(year=2000)

    with pytest.raises(WorkspaceConcurrencyConflictError):
        await use_case.execute(
            UpdateWorkspaceCommand(
                workspace_id=workspace.id,
                actor_user_id=UUID(int=999),
                name="Acme Corp",
                settings={},
                model_policy={},
                expected_updated_at=stale,
            )
        )


async def test_update_workspace_raises_not_found_for_unknown_id() -> None:
    use_case = UpdateWorkspace(
        workspaces=FakeWorkspaceRepository(), audit_log=FakeAuditLog(), ids=FakeIdGenerator()
    )
    with pytest.raises(WorkspaceNotFoundError):
        await use_case.execute(
            UpdateWorkspaceCommand(
                workspace_id=UUID(int=1),
                actor_user_id=UUID(int=999),
                name="x",
                settings={},
                model_policy={},
                expected_updated_at=datetime.now(UTC),
            )
        )


async def test_delete_workspace_makes_it_unreadable() -> None:
    workspaces = FakeWorkspaceRepository()
    workspace = await workspaces.create(
        id=UUID(int=1), name="Acme", slug="acme", settings={}, model_policy={}
    )
    delete_use_case = DeleteWorkspace(
        workspaces=workspaces,
        audit_log=FakeAuditLog(),
        clock=FakeClock(start=datetime.now(UTC)),
        ids=FakeIdGenerator(),
    )
    await delete_use_case.execute(
        DeleteWorkspaceCommand(workspace_id=workspace.id, actor_user_id=UUID(int=999))
    )

    with pytest.raises(WorkspaceNotFoundError):
        await GetWorkspace(workspaces=workspaces).execute(
            GetWorkspaceCommand(workspace_id=workspace.id)
        )

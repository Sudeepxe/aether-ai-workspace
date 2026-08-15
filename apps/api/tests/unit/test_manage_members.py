from __future__ import annotations

from uuid import UUID

import pytest

from aether.app.workspaces.manage_members import (
    ListMembers,
    ListMembersCommand,
    RemoveMember,
    RemoveMemberCommand,
    UpdateMemberRole,
    UpdateMemberRoleCommand,
)
from aether.domain.entities import MembershipRole
from aether.domain.errors import LastOwnerProtectionError, MembershipNotFoundError
from tests.unit.fakes.auth import FakeIdGenerator
from tests.unit.fakes.workspaces import FakeAuditLog, FakeMembershipRepository

pytestmark = pytest.mark.unit

WORKSPACE = UUID(int=1)
OWNER = UUID(int=1)
OTHER = UUID(int=2)


async def _seeded_memberships(*, owners: int = 1, members: int = 0) -> FakeMembershipRepository:
    repo = FakeMembershipRepository()
    for i in range(owners):
        await repo.create(
            id=UUID(int=100 + i),
            workspace_id=WORKSPACE,
            user_id=UUID(int=1000 + i),
            role=MembershipRole.OWNER,
        )
    for i in range(members):
        await repo.create(
            id=UUID(int=200 + i),
            workspace_id=WORKSPACE,
            user_id=UUID(int=2000 + i),
            role=MembershipRole.MEMBER,
        )
    return repo


async def test_list_members_returns_all_members_of_the_workspace() -> None:
    memberships = await _seeded_memberships(owners=1, members=2)
    result = await ListMembers(memberships=memberships).execute(
        ListMembersCommand(workspace_id=WORKSPACE)
    )
    assert len(result) == 3


async def test_update_member_role_succeeds_when_not_the_last_owner() -> None:
    memberships = await _seeded_memberships(owners=2, members=0)
    audit_log = FakeAuditLog()
    use_case = UpdateMemberRole(memberships=memberships, audit_log=audit_log, ids=FakeIdGenerator())

    updated = await use_case.execute(
        UpdateMemberRoleCommand(
            workspace_id=WORKSPACE,
            target_user_id=UUID(int=1001),
            new_role=MembershipRole.ADMIN,
            actor_user_id=UUID(int=1000),
        )
    )

    assert updated.role == MembershipRole.ADMIN
    assert audit_log.recorded[0].action == "membership.role_changed"


async def test_update_member_role_refuses_to_demote_the_last_owner() -> None:
    memberships = await _seeded_memberships(owners=1, members=0)
    use_case = UpdateMemberRole(
        memberships=memberships, audit_log=FakeAuditLog(), ids=FakeIdGenerator()
    )

    with pytest.raises(LastOwnerProtectionError):
        await use_case.execute(
            UpdateMemberRoleCommand(
                workspace_id=WORKSPACE,
                target_user_id=UUID(int=1000),
                new_role=MembershipRole.ADMIN,
                actor_user_id=UUID(int=1000),
            )
        )


async def test_update_member_role_raises_not_found_for_non_member() -> None:
    memberships = await _seeded_memberships(owners=1)
    use_case = UpdateMemberRole(
        memberships=memberships, audit_log=FakeAuditLog(), ids=FakeIdGenerator()
    )
    with pytest.raises(MembershipNotFoundError):
        await use_case.execute(
            UpdateMemberRoleCommand(
                workspace_id=WORKSPACE,
                target_user_id=OTHER,
                new_role=MembershipRole.ADMIN,
                actor_user_id=OWNER,
            )
        )


async def test_remove_member_succeeds_for_a_regular_member() -> None:
    memberships = await _seeded_memberships(owners=1, members=1)
    audit_log = FakeAuditLog()
    use_case = RemoveMember(memberships=memberships, audit_log=audit_log, ids=FakeIdGenerator())

    await use_case.execute(
        RemoveMemberCommand(
            workspace_id=WORKSPACE, target_user_id=UUID(int=2000), actor_user_id=UUID(int=1000)
        )
    )

    assert await memberships.get(WORKSPACE, UUID(int=2000)) is None
    assert audit_log.recorded[0].action == "membership.removed"


async def test_remove_member_refuses_to_remove_the_last_owner() -> None:
    memberships = await _seeded_memberships(owners=1, members=0)
    use_case = RemoveMember(
        memberships=memberships, audit_log=FakeAuditLog(), ids=FakeIdGenerator()
    )

    with pytest.raises(LastOwnerProtectionError):
        await use_case.execute(
            RemoveMemberCommand(
                workspace_id=WORKSPACE, target_user_id=UUID(int=1000), actor_user_id=UUID(int=1000)
            )
        )

    # The owner must still actually be there — the guard isn't cosmetic.
    assert await memberships.get(WORKSPACE, UUID(int=1000)) is not None


async def test_remove_member_succeeds_removing_one_owner_when_two_exist() -> None:
    memberships = await _seeded_memberships(owners=2, members=0)
    use_case = RemoveMember(
        memberships=memberships, audit_log=FakeAuditLog(), ids=FakeIdGenerator()
    )

    await use_case.execute(
        RemoveMemberCommand(
            workspace_id=WORKSPACE, target_user_id=UUID(int=1001), actor_user_id=UUID(int=1000)
        )
    )

    assert await memberships.get(WORKSPACE, UUID(int=1001)) is None
    assert await memberships.get(WORKSPACE, UUID(int=1000)) is not None

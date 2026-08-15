from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from aether.app.auth.tokens import hash_token
from aether.app.invitations.accept_invitation import AcceptInvitation, AcceptInvitationCommand
from aether.app.invitations.create_invitation import CreateInvitation, CreateInvitationCommand
from aether.app.invitations.revoke_invitation import RevokeInvitation, RevokeInvitationCommand
from aether.app.notifications.dispatch_email_outbox import EMAIL_SEND_EVENT_TYPE
from aether.domain.entities import MembershipRole
from aether.domain.errors import InvalidInvitationError
from tests.unit.fakes.auth import FakeClock, FakeIdGenerator
from tests.unit.fakes.outbox import FakeOutboxRepository
from tests.unit.fakes.workspaces import (
    FakeAuditLog,
    FakeInvitationRepository,
    FakeMembershipRepository,
)

pytestmark = pytest.mark.unit

WORKSPACE = UUID(int=1)
INVITER = UUID(int=1)
INVITEE = UUID(int=2)


async def test_create_invitation_stores_only_the_hash_never_the_raw_token() -> None:
    invitations = FakeInvitationRepository()
    audit_log = FakeAuditLog()
    outbox = FakeOutboxRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    use_case = CreateInvitation(
        invitations=invitations,
        audit_log=audit_log,
        outbox=outbox,
        clock=clock,
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute(
        CreateInvitationCommand(
            workspace_id=WORKSPACE,
            email="invitee@example.com",
            role=MembershipRole.MEMBER,
            invited_by=INVITER,
        )
    )

    assert result.invitation.token_hash != result.raw_token
    assert result.invitation.token_hash == hash_token(result.raw_token)
    assert result.invitation.expires_at == clock.now() + timedelta(days=7)
    assert audit_log.recorded[0].action == "invitation.created"

    pending = await outbox.fetch_pending(event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=5, limit=10)
    assert len(pending) == 1
    assert pending[0].payload["to"] == "invitee@example.com"
    assert pending[0].tenant_id == WORKSPACE
    # The raw token must appear only in the queued email payload, never
    # anywhere the invitation itself (or its audit trail) is readable.
    assert result.raw_token in pending[0].payload["text_body"]


async def test_accept_invitation_creates_membership_and_consumes_token() -> None:
    invitations = FakeInvitationRepository()
    memberships = FakeMembershipRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    raw_token = "raw-token-value"
    await invitations.create(
        id=UUID(int=1),
        workspace_id=WORKSPACE,
        email="invitee@example.com",
        role=MembershipRole.ADMIN,
        token_hash=hash_token(raw_token),
        invited_by=INVITER,
        expires_at=clock.now() + timedelta(days=7),
    )
    use_case = AcceptInvitation(
        invitations=invitations,
        memberships=memberships,
        audit_log=FakeAuditLog(),
        clock=clock,
        ids=FakeIdGenerator(),
    )

    membership = await use_case.execute(
        AcceptInvitationCommand(raw_token=raw_token, accepting_user_id=INVITEE)
    )

    assert membership.role == MembershipRole.ADMIN
    assert membership.user_id == INVITEE
    stored = await invitations.get_by_token_hash(hash_token(raw_token))
    assert stored is not None
    assert stored.consumed_at == clock.now()


async def test_accept_invitation_rejects_unknown_token() -> None:
    use_case = AcceptInvitation(
        invitations=FakeInvitationRepository(),
        memberships=FakeMembershipRepository(),
        audit_log=FakeAuditLog(),
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=FakeIdGenerator(),
    )
    with pytest.raises(InvalidInvitationError):
        await use_case.execute(
            AcceptInvitationCommand(raw_token="never-issued", accepting_user_id=INVITEE)
        )


async def test_accept_invitation_rejects_expired_token() -> None:
    invitations = FakeInvitationRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    raw_token = "raw-token-value"
    await invitations.create(
        id=UUID(int=1),
        workspace_id=WORKSPACE,
        email="invitee@example.com",
        role=MembershipRole.MEMBER,
        token_hash=hash_token(raw_token),
        invited_by=INVITER,
        expires_at=clock.now() - timedelta(seconds=1),  # already expired
    )
    use_case = AcceptInvitation(
        invitations=invitations,
        memberships=FakeMembershipRepository(),
        audit_log=FakeAuditLog(),
        clock=clock,
        ids=FakeIdGenerator(),
    )
    with pytest.raises(InvalidInvitationError):
        await use_case.execute(
            AcceptInvitationCommand(raw_token=raw_token, accepting_user_id=INVITEE)
        )


async def test_accept_invitation_rejects_already_consumed_token() -> None:
    invitations = FakeInvitationRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    raw_token = "raw-token-value"
    invitation = await invitations.create(
        id=UUID(int=1),
        workspace_id=WORKSPACE,
        email="invitee@example.com",
        role=MembershipRole.MEMBER,
        token_hash=hash_token(raw_token),
        invited_by=INVITER,
        expires_at=clock.now() + timedelta(days=7),
    )
    await invitations.consume(invitation.id, consumed_at=clock.now())
    use_case = AcceptInvitation(
        invitations=invitations,
        memberships=FakeMembershipRepository(),
        audit_log=FakeAuditLog(),
        clock=clock,
        ids=FakeIdGenerator(),
    )
    with pytest.raises(InvalidInvitationError):
        await use_case.execute(
            AcceptInvitationCommand(raw_token=raw_token, accepting_user_id=INVITEE)
        )


async def test_accept_invitation_is_idempotent_if_already_a_member() -> None:
    invitations = FakeInvitationRepository()
    memberships = FakeMembershipRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    existing = await memberships.create(
        id=UUID(int=50), workspace_id=WORKSPACE, user_id=INVITEE, role=MembershipRole.MEMBER
    )
    raw_token = "raw-token-value"
    await invitations.create(
        id=UUID(int=1),
        workspace_id=WORKSPACE,
        email="invitee@example.com",
        role=MembershipRole.ADMIN,
        token_hash=hash_token(raw_token),
        invited_by=INVITER,
        expires_at=clock.now() + timedelta(days=7),
    )
    use_case = AcceptInvitation(
        invitations=invitations,
        memberships=memberships,
        audit_log=FakeAuditLog(),
        clock=clock,
        ids=FakeIdGenerator(),
    )

    result = await use_case.execute(
        AcceptInvitationCommand(raw_token=raw_token, accepting_user_id=INVITEE)
    )

    # No duplicate membership created, and the existing role isn't
    # silently upgraded to what the invitation offered.
    assert result.id == existing.id
    assert result.role == MembershipRole.MEMBER


async def test_revoke_invitation_deletes_it_and_records_audit_event() -> None:
    invitations = FakeInvitationRepository()
    audit_log = FakeAuditLog()
    invitation = await invitations.create(
        id=UUID(int=1),
        workspace_id=WORKSPACE,
        email="invitee@example.com",
        role=MembershipRole.MEMBER,
        token_hash="irrelevant",
        invited_by=INVITER,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    use_case = RevokeInvitation(invitations=invitations, audit_log=audit_log, ids=FakeIdGenerator())

    await use_case.execute(
        RevokeInvitationCommand(
            workspace_id=WORKSPACE, invitation_id=invitation.id, actor_user_id=INVITER
        )
    )

    assert await invitations.get_by_token_hash("irrelevant") is None
    assert audit_log.recorded[0].action == "invitation.revoked"

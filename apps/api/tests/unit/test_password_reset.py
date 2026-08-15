from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aether.app.auth.revoke_user_sessions import RevokeUserSessions
from aether.app.auth.tokens import hash_token
from aether.app.notifications.dispatch_email_outbox import EMAIL_SEND_EVENT_TYPE
from aether.app.password_reset.request_password_reset import (
    RequestPasswordReset,
    RequestPasswordResetCommand,
)
from aether.app.password_reset.reset_password import ResetPassword, ResetPasswordCommand
from aether.domain.errors import InvalidPasswordResetTokenError
from tests.unit.fakes.auth import (
    FakeClock,
    FakeIdGenerator,
    FakePasswordHasher,
    FakeRefreshTokenRepository,
    FakeUserRepository,
)
from tests.unit.fakes.outbox import FakeOutboxRepository
from tests.unit.fakes.password_reset import FakePasswordResetTokenRepository
from tests.unit.fakes.workspaces import FakeAuditLog

pytestmark = pytest.mark.unit


async def test_request_reset_enqueues_email_for_a_known_user() -> None:
    users = FakeUserRepository()
    hasher = FakePasswordHasher()
    ids = FakeIdGenerator()
    await users.create(
        id=ids.new_id(),
        email="a@example.com",
        display_name="A",
        password_hash=hasher.hash("old-password"),
    )
    tokens = FakePasswordResetTokenRepository()
    outbox = FakeOutboxRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    use_case = RequestPasswordReset(
        users=users, password_reset_tokens=tokens, outbox=outbox, clock=clock, ids=ids
    )

    await use_case.execute(RequestPasswordResetCommand(email="a@example.com"))

    pending = await outbox.fetch_pending(event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=5, limit=10)
    assert len(pending) == 1
    assert pending[0].payload["to"] == "a@example.com"
    assert pending[0].tenant_id is None


async def test_request_reset_is_a_silent_noop_for_unknown_email() -> None:
    """Enumeration-safety: no token row, no queued email — nothing an
    attacker could observe distinguishes this from the known-user path
    at the HTTP layer, which returns 202 either way."""
    tokens = FakePasswordResetTokenRepository()
    outbox = FakeOutboxRepository()
    use_case = RequestPasswordReset(
        users=FakeUserRepository(),
        password_reset_tokens=tokens,
        outbox=outbox,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=FakeIdGenerator(),
    )

    await use_case.execute(RequestPasswordResetCommand(email="nobody@example.com"))

    assert tokens.created == []
    assert (
        await outbox.fetch_pending(event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=5, limit=10) == []
    )


async def test_request_reset_is_a_silent_noop_for_oauth_only_account() -> None:
    """An account with no password (password_hash=None) has nothing to
    reset — same enumeration-safe no-op as an unknown email."""
    users = FakeUserRepository()
    ids = FakeIdGenerator()
    await users.create(
        id=ids.new_id(), email="oauth@example.com", display_name="O", password_hash=None
    )
    outbox = FakeOutboxRepository()
    use_case = RequestPasswordReset(
        users=users,
        password_reset_tokens=FakePasswordResetTokenRepository(),
        outbox=outbox,
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=ids,
    )

    await use_case.execute(RequestPasswordResetCommand(email="oauth@example.com"))

    assert (
        await outbox.fetch_pending(event_type=EMAIL_SEND_EVENT_TYPE, max_attempts=5, limit=10) == []
    )


def _reset_password_use_case(
    *,
    users: FakeUserRepository,
    tokens: FakePasswordResetTokenRepository,
    clock: FakeClock,
    ids: FakeIdGenerator,
) -> tuple[ResetPassword, FakeAuditLog]:
    refresh_tokens = FakeRefreshTokenRepository()
    revoke_sessions = RevokeUserSessions(refresh_tokens=refresh_tokens, clock=clock)
    audit_log = FakeAuditLog()
    use_case = ResetPassword(
        users=users,
        password_reset_tokens=tokens,
        hasher=FakePasswordHasher(),
        revoke_user_sessions=revoke_sessions,
        audit_log=audit_log,
        clock=clock,
        ids=ids,
    )
    return use_case, audit_log


async def test_reset_password_updates_hash_consumes_token_and_revokes_sessions() -> None:
    users = FakeUserRepository()
    hasher = FakePasswordHasher()
    ids = FakeIdGenerator()
    user = await users.create(
        id=ids.new_id(), email="a@example.com", display_name="A", password_hash=hasher.hash("old")
    )
    tokens = FakePasswordResetTokenRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    raw_token = "reset-token-value"
    await tokens.create(
        id=ids.new_id(),
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=clock.now() + timedelta(minutes=30),
    )
    use_case, audit_log = _reset_password_use_case(users=users, tokens=tokens, clock=clock, ids=ids)

    returned_user_id = await use_case.execute(
        ResetPasswordCommand(raw_token=raw_token, new_password="brand-new-password")
    )

    assert returned_user_id == user.id
    updated_user = await users.get_by_id(user.id)
    assert updated_user is not None
    assert hasher.verify("brand-new-password", updated_user.password_hash or "")
    assert not hasher.verify("old", updated_user.password_hash or "")
    stored_token = await tokens.get_by_token_hash(hash_token(raw_token))
    assert stored_token is not None
    assert stored_token.consumed_at == clock.now()
    assert audit_log.recorded[0].action == "auth.password_reset_completed"


async def test_reset_password_rejects_unknown_token() -> None:
    use_case, _ = _reset_password_use_case(
        users=FakeUserRepository(),
        tokens=FakePasswordResetTokenRepository(),
        clock=FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
        ids=FakeIdGenerator(),
    )
    with pytest.raises(InvalidPasswordResetTokenError):
        await use_case.execute(
            ResetPasswordCommand(raw_token="never-issued", new_password="x" * 10)
        )


async def test_reset_password_rejects_expired_token() -> None:
    users = FakeUserRepository()
    ids = FakeIdGenerator()
    user = await users.create(
        id=ids.new_id(), email="a@example.com", display_name="A", password_hash="h"
    )
    tokens = FakePasswordResetTokenRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    raw_token = "reset-token-value"
    await tokens.create(
        id=ids.new_id(),
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=clock.now() - timedelta(seconds=1),
    )
    use_case, _ = _reset_password_use_case(users=users, tokens=tokens, clock=clock, ids=ids)

    with pytest.raises(InvalidPasswordResetTokenError):
        await use_case.execute(ResetPasswordCommand(raw_token=raw_token, new_password="x" * 10))


async def test_reset_password_rejects_a_second_use_of_the_same_token() -> None:
    users = FakeUserRepository()
    ids = FakeIdGenerator()
    user = await users.create(
        id=ids.new_id(), email="a@example.com", display_name="A", password_hash="h"
    )
    tokens = FakePasswordResetTokenRepository()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    raw_token = "reset-token-value"
    await tokens.create(
        id=ids.new_id(),
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=clock.now() + timedelta(minutes=30),
    )
    use_case, _ = _reset_password_use_case(users=users, tokens=tokens, clock=clock, ids=ids)
    await use_case.execute(
        ResetPasswordCommand(raw_token=raw_token, new_password="first-new-password")
    )

    with pytest.raises(InvalidPasswordResetTokenError):
        await use_case.execute(
            ResetPasswordCommand(raw_token=raw_token, new_password="second-new-password")
        )


async def test_reset_password_revokes_every_refresh_family_for_the_user() -> None:
    users = FakeUserRepository()
    ids = FakeIdGenerator()
    user = await users.create(
        id=ids.new_id(), email="a@example.com", display_name="A", password_hash="h"
    )
    refresh_tokens = FakeRefreshTokenRepository()
    family_a, family_b = ids.new_id(), ids.new_id()
    clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=user.id,
        family_id=family_a,
        token_hash="hash-a",
        device_fingerprint="dev-a",
        expires_at=clock.now() + timedelta(days=7),
    )
    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=user.id,
        family_id=family_b,
        token_hash="hash-b",
        device_fingerprint="dev-b",
        expires_at=clock.now() + timedelta(days=7),
    )
    tokens = FakePasswordResetTokenRepository()
    raw_token = "reset-token-value"
    await tokens.create(
        id=ids.new_id(),
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=clock.now() + timedelta(minutes=30),
    )
    revoke_sessions = RevokeUserSessions(refresh_tokens=refresh_tokens, clock=clock)
    use_case = ResetPassword(
        users=users,
        password_reset_tokens=tokens,
        hasher=FakePasswordHasher(),
        revoke_user_sessions=revoke_sessions,
        audit_log=FakeAuditLog(),
        clock=clock,
        ids=ids,
    )

    await use_case.execute(ResetPasswordCommand(raw_token=raw_token, new_password="new-password"))

    token_a = await refresh_tokens.get_by_hash("hash-a")
    token_b = await refresh_tokens.get_by_hash("hash-b")
    assert token_a is not None and token_a.revoked_at == clock.now()
    assert token_b is not None and token_b.revoked_at == clock.now()

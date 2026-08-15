from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aether.app.auth.logout_user import LogoutUser, LogoutUserCommand
from aether.app.auth.revoke_user_sessions import RevokeUserSessions, RevokeUserSessionsCommand
from aether.app.auth.tokens import hash_refresh_token
from tests.unit.fakes.auth import (
    FakeClock,
    FakeIdGenerator,
    FakeRefreshTokenRepository,
    FakeRevocationPort,
)
from tests.unit.fakes.workspaces import FakeAuditLog

pytestmark = pytest.mark.unit

_START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_logout_denies_jti_and_revokes_refresh_family() -> None:
    clock = FakeClock(start=_START)
    refresh_tokens = FakeRefreshTokenRepository()
    revocations = FakeRevocationPort()
    ids = FakeIdGenerator()
    user_id = ids.new_id()
    family_id = ids.new_id()
    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_refresh_token("raw-token-1"),
        device_fingerprint="dev-1",
        expires_at=_START + timedelta(days=7),
    )

    use_case = LogoutUser(
        refresh_tokens=refresh_tokens,
        revocations=revocations,
        clock=clock,
        audit_log=FakeAuditLog(),
        ids=ids,
    )
    jti = ids.new_id()
    await use_case.execute(
        LogoutUserCommand(
            user_id=user_id,
            jti=jti,
            access_token_expires_at=_START + timedelta(minutes=15),
            raw_refresh_token="raw-token-1",
        )
    )

    assert await revocations.is_denied(jti) is True
    token = await refresh_tokens.get_by_hash(hash_refresh_token("raw-token-1"))
    assert token is not None
    assert token.revoked_at == _START


async def test_revoke_user_sessions_revokes_every_family_for_the_user() -> None:
    clock = FakeClock(start=_START)
    refresh_tokens = FakeRefreshTokenRepository()
    ids = FakeIdGenerator()
    user_id = ids.new_id()
    other_user_id = ids.new_id()

    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=user_id,
        family_id=ids.new_id(),
        token_hash=hash_refresh_token("raw-a"),
        device_fingerprint="dev-1",
        expires_at=_START + timedelta(days=7),
    )
    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=user_id,
        family_id=ids.new_id(),
        token_hash=hash_refresh_token("raw-b"),
        device_fingerprint="dev-2",
        expires_at=_START + timedelta(days=7),
    )
    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=other_user_id,
        family_id=ids.new_id(),
        token_hash=hash_refresh_token("raw-c"),
        device_fingerprint="dev-3",
        expires_at=_START + timedelta(days=7),
    )

    use_case = RevokeUserSessions(refresh_tokens=refresh_tokens, clock=clock)
    await use_case.execute(RevokeUserSessionsCommand(user_id=user_id))

    token_a = await refresh_tokens.get_by_hash(hash_refresh_token("raw-a"))
    token_b = await refresh_tokens.get_by_hash(hash_refresh_token("raw-b"))
    token_c = await refresh_tokens.get_by_hash(hash_refresh_token("raw-c"))
    assert token_a is not None and token_a.revoked_at == _START
    assert token_b is not None and token_b.revoked_at == _START
    assert token_c is not None and token_c.revoked_at is None  # a different user, untouched

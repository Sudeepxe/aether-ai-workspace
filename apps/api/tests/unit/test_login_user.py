from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aether.app.auth.login_user import LoginUser, LoginUserCommand
from aether.app.auth.tokens import hash_refresh_token
from aether.domain.errors import AuthenticationFailedError
from tests.unit.fakes.auth import (
    FakeClock,
    FakeIdGenerator,
    FakePasswordHasher,
    FakeRefreshTokenRepository,
    FakeTokenPort,
    FakeUserRepository,
)
from tests.unit.fakes.workspaces import FakeAuditLog

pytestmark = pytest.mark.unit

_START = datetime(2026, 1, 1, tzinfo=UTC)


async def _seeded_use_case() -> tuple[LoginUser, FakeUserRepository, FakeRefreshTokenRepository]:
    users = FakeUserRepository()
    refresh_tokens = FakeRefreshTokenRepository()
    hasher = FakePasswordHasher()
    ids = FakeIdGenerator()
    await users.create(
        id=ids.new_id(),
        email="a@example.com",
        display_name="A",
        password_hash=hasher.hash("correct-password"),
    )
    clock = FakeClock(start=_START)
    use_case = LoginUser(
        users=users,
        refresh_tokens=refresh_tokens,
        hasher=hasher,
        tokens=FakeTokenPort(clock=clock),
        clock=clock,
        ids=ids,
        audit_log=FakeAuditLog(),
        refresh_ttl_seconds=604_800,
    )
    return use_case, users, refresh_tokens


async def test_login_with_correct_password_succeeds() -> None:
    use_case, _, refresh_tokens = await _seeded_use_case()
    result = await use_case.execute(
        LoginUserCommand(
            email="a@example.com", password="correct-password", device_fingerprint="dev-1"
        )
    )
    assert result.access_token
    assert result.refresh_token
    stored = await refresh_tokens.get_by_hash(hash_refresh_token(result.refresh_token))
    assert stored is not None
    assert stored.user_id == result.user_id


async def test_login_with_wrong_password_raises_authentication_failed() -> None:
    use_case, _, _ = await _seeded_use_case()
    with pytest.raises(AuthenticationFailedError):
        await use_case.execute(
            LoginUserCommand(
                email="a@example.com", password="wrong-password", device_fingerprint="dev-1"
            )
        )


async def test_login_with_unknown_email_raises_same_error_as_wrong_password() -> None:
    """Enumeration-safety: identical exception type/message for both cases."""
    use_case, _, _ = await _seeded_use_case()
    with pytest.raises(AuthenticationFailedError):
        await use_case.execute(
            LoginUserCommand(
                email="nobody@example.com", password="anything", device_fingerprint="dev-1"
            )
        )

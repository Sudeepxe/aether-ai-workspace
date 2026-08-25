from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aether.app.auth.refresh_session import RefreshSession, RefreshSessionCommand
from aether.app.auth.tokens import hash_refresh_token
from aether.domain.errors import InvalidRefreshTokenError, RefreshTokenReusedError
from aether.observability.metrics import AUTH_REFRESH_REUSE_TOTAL
from tests.unit.fakes.auth import (
    FakeClock,
    FakeIdGenerator,
    FakeRefreshTokenRepository,
    FakeTokenPort,
)

pytestmark = pytest.mark.unit

_START = datetime(2026, 1, 1, tzinfo=UTC)
_USER_ID = FakeIdGenerator().new_id()


def _use_case(
    clock: FakeClock,
) -> tuple[RefreshSession, FakeRefreshTokenRepository, FakeIdGenerator]:
    refresh_tokens = FakeRefreshTokenRepository()
    ids = FakeIdGenerator()
    use_case = RefreshSession(
        refresh_tokens=refresh_tokens,
        tokens=FakeTokenPort(clock=clock),
        clock=clock,
        ids=ids,
        refresh_ttl_seconds=604_800,
        grace_seconds=30,
    )
    return use_case, refresh_tokens, ids


async def _seed_token(
    refresh_tokens: FakeRefreshTokenRepository, ids: FakeIdGenerator, *, raw: str, device: str
) -> None:
    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=_USER_ID,
        family_id=ids.new_id(),
        token_hash=hash_refresh_token(raw),
        device_fingerprint=device,
        expires_at=_START + timedelta(days=7),
    )


async def test_unused_token_rotates_and_returns_new_pair() -> None:
    clock = FakeClock(start=_START)
    use_case, refresh_tokens, ids = _use_case(clock)
    await _seed_token(refresh_tokens, ids, raw="raw-token-1", device="dev-1")

    result = await use_case.execute(
        RefreshSessionCommand(raw_refresh_token="raw-token-1", device_fingerprint="dev-1")
    )

    assert result.access_token
    assert result.refresh_token is not None
    assert result.refresh_token != "raw-token-1"

    old = await refresh_tokens.get_by_hash(hash_refresh_token("raw-token-1"))
    assert old is not None
    assert old.used_at == _START
    assert old.successor_id is not None


async def test_reused_token_within_grace_same_device_returns_new_access_token_no_refresh() -> None:
    clock = FakeClock(start=_START)
    use_case, refresh_tokens, ids = _use_case(clock)
    await _seed_token(refresh_tokens, ids, raw="raw-token-1", device="dev-1")

    first = await use_case.execute(
        RefreshSessionCommand(raw_refresh_token="raw-token-1", device_fingerprint="dev-1")
    )
    clock.advance(timedelta(seconds=5))  # well within the 30s grace window

    second = await use_case.execute(
        RefreshSessionCommand(raw_refresh_token="raw-token-1", device_fingerprint="dev-1")
    )

    assert second.access_token != first.access_token  # a fresh access token is still issued
    assert second.refresh_token is None  # but no second successor is minted

    old = await refresh_tokens.get_by_hash(hash_refresh_token("raw-token-1"))
    assert old is not None
    assert old.revoked_at is None  # the grace-window replay must NOT trip revocation


async def test_reused_token_outside_grace_window_revokes_family() -> None:
    clock = FakeClock(start=_START)
    use_case, refresh_tokens, ids = _use_case(clock)
    await _seed_token(refresh_tokens, ids, raw="raw-token-1", device="dev-1")

    await use_case.execute(
        RefreshSessionCommand(raw_refresh_token="raw-token-1", device_fingerprint="dev-1")
    )
    clock.advance(timedelta(seconds=31))  # just past the 30s grace window

    before = AUTH_REFRESH_REUSE_TOTAL._value.get()
    with pytest.raises(RefreshTokenReusedError):
        await use_case.execute(
            RefreshSessionCommand(raw_refresh_token="raw-token-1", device_fingerprint="dev-1")
        )

    old = await refresh_tokens.get_by_hash(hash_refresh_token("raw-token-1"))
    assert old is not None
    assert old.revoked_at == _START + timedelta(seconds=31)
    # S9 #96's AuthRefreshReuseDetected page-grade alert needs a real
    # detection point behind it, not just a rule that can never fire.
    assert AUTH_REFRESH_REUSE_TOTAL._value.get() == before + 1


async def test_reused_token_from_different_device_revokes_family_even_within_grace() -> None:
    clock = FakeClock(start=_START)
    use_case, refresh_tokens, ids = _use_case(clock)
    await _seed_token(refresh_tokens, ids, raw="raw-token-1", device="dev-1")

    await use_case.execute(
        RefreshSessionCommand(raw_refresh_token="raw-token-1", device_fingerprint="dev-1")
    )
    clock.advance(timedelta(seconds=5))  # within grace, but...

    with pytest.raises(RefreshTokenReusedError):
        await use_case.execute(
            # ...a different device presents the same already-used token.
            RefreshSessionCommand(
                raw_refresh_token="raw-token-1", device_fingerprint="attacker-device"
            )
        )

    old = await refresh_tokens.get_by_hash(hash_refresh_token("raw-token-1"))
    assert old is not None
    assert old.revoked_at is not None


async def test_unknown_token_raises_invalid_refresh_token() -> None:
    clock = FakeClock(start=_START)
    use_case, _, _ = _use_case(clock)
    with pytest.raises(InvalidRefreshTokenError):
        await use_case.execute(
            RefreshSessionCommand(raw_refresh_token="never-issued", device_fingerprint="dev-1")
        )


async def test_expired_token_raises_invalid_refresh_token() -> None:
    clock = FakeClock(start=_START)
    use_case, refresh_tokens, ids = _use_case(clock)
    await refresh_tokens.create(
        id=ids.new_id(),
        user_id=_USER_ID,
        family_id=ids.new_id(),
        token_hash=hash_refresh_token("raw-token-1"),
        device_fingerprint="dev-1",
        expires_at=_START - timedelta(seconds=1),  # already expired
    )
    with pytest.raises(InvalidRefreshTokenError):
        await use_case.execute(
            RefreshSessionCommand(raw_refresh_token="raw-token-1", device_fingerprint="dev-1")
        )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from aether.domain.entities import RefreshToken, User
from aether.domain.errors import EmailAlreadyRegisteredError
from aether.ports.security import AccessTokenClaims


class FakeClock:
    def __init__(self, *, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeIdGenerator:
    def __init__(self) -> None:
        self._next = 0

    def new_id(self) -> UUID:
        self._next += 1
        # Deterministic, distinct, valid UUIDs — easy to assert on in tests.
        return UUID(int=self._next)


class FakePasswordHasher:
    """`hash` is a trivial reversible marker — never use outside tests."""

    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password: str, password_hash: str) -> bool:
        return password_hash == f"hashed:{password}"


class FakeTokenPort:
    def __init__(self, *, clock: FakeClock, access_ttl_seconds: int = 900) -> None:
        self._clock = clock
        self._ttl = timedelta(seconds=access_ttl_seconds)
        self.issued: list[tuple[UUID, UUID]] = []

    def issue_access_token(self, *, user_id: UUID, jti: UUID) -> str:
        self.issued.append((user_id, jti))
        now = self._clock.now()
        return (
            f"fake-jwt:{user_id}:{jti}:{int(now.timestamp())}:{int((now + self._ttl).timestamp())}"
        )

    def verify_access_token(self, token: str) -> AccessTokenClaims:
        _, user_id, jti, iat, exp = token.split(":")
        return AccessTokenClaims(
            sub=UUID(user_id),
            jti=UUID(jti),
            issued_at=datetime.fromtimestamp(int(iat), tz=self._clock.now().tzinfo),
            expires_at=datetime.fromtimestamp(int(exp), tz=self._clock.now().tzinfo),
        )


@dataclass
class FakeUserRepository:
    _by_id: dict[UUID, User] = field(default_factory=dict)
    _by_email: dict[str, User] = field(default_factory=dict)

    async def create(
        self, *, id: UUID, email: str, display_name: str, password_hash: str | None
    ) -> User:
        if email in self._by_email:
            raise EmailAlreadyRegisteredError(email)
        now = datetime(2026, 1, 1)
        user = User(
            id=id,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            status="active",
            created_at=now,
            updated_at=now,
        )
        self._by_id[id] = user
        self._by_email[email] = user
        return user

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._by_id.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return self._by_email.get(email)


@dataclass
class FakeRefreshTokenRepository:
    _by_id: dict[UUID, RefreshToken] = field(default_factory=dict)
    _by_hash: dict[str, UUID] = field(default_factory=dict)

    async def create(
        self,
        *,
        id: UUID,
        user_id: UUID,
        family_id: UUID,
        token_hash: str,
        device_fingerprint: str,
        expires_at: datetime,
    ) -> RefreshToken:
        token = RefreshToken(
            id=id,
            user_id=user_id,
            family_id=family_id,
            token_hash=token_hash,
            device_fingerprint=device_fingerprint,
            used_at=None,
            successor_id=None,
            expires_at=expires_at,
            revoked_at=None,
            created_at=expires_at,
        )
        self._by_id[id] = token
        self._by_hash[token_hash] = id
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        token_id = self._by_hash.get(token_hash)
        return self._by_id.get(token_id) if token_id is not None else None

    async def mark_used(self, token_id: UUID, *, successor_id: UUID, used_at: datetime) -> None:
        token = self._by_id[token_id]
        self._by_id[token_id] = RefreshToken(
            id=token.id,
            user_id=token.user_id,
            family_id=token.family_id,
            token_hash=token.token_hash,
            device_fingerprint=token.device_fingerprint,
            used_at=used_at,
            successor_id=successor_id,
            expires_at=token.expires_at,
            revoked_at=token.revoked_at,
            created_at=token.created_at,
        )

    async def revoke_family(self, family_id: UUID, *, revoked_at: datetime) -> None:
        for token_id, token in list(self._by_id.items()):
            if token.family_id == family_id and token.revoked_at is None:
                self._by_id[token_id] = _with_revoked(token, revoked_at)

    async def revoke_all_for_user(self, user_id: UUID, *, revoked_at: datetime) -> None:
        for token_id, token in list(self._by_id.items()):
            if token.user_id == user_id and token.revoked_at is None:
                self._by_id[token_id] = _with_revoked(token, revoked_at)


def _with_revoked(token: RefreshToken, revoked_at: datetime) -> RefreshToken:
    return RefreshToken(
        id=token.id,
        user_id=token.user_id,
        family_id=token.family_id,
        token_hash=token.token_hash,
        device_fingerprint=token.device_fingerprint,
        used_at=token.used_at,
        successor_id=token.successor_id,
        expires_at=token.expires_at,
        revoked_at=revoked_at,
        created_at=token.created_at,
    )


class FakeRevocationPort:
    def __init__(self) -> None:
        self._denied: dict[UUID, int] = {}

    async def deny(self, jti: UUID, *, ttl_seconds: int) -> None:
        self._denied[jti] = ttl_seconds

    async def is_denied(self, jti: UUID) -> bool:
        return jti in self._denied


def new_uuid() -> UUID:
    return uuid4()

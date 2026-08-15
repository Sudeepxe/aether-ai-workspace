"""Composition root — the one place allowed to import adapters directly
and wire them to ports (Blueprint §3.3: "nothing imports adapters except
the composition root"). Constructed once at process startup (see
``http/app.py``'s lifespan) and threaded through FastAPI's dependency
system via ``request.app.state.container``.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import redis.asyncio as redis_asyncio

from aether.adapters.argon2.hasher import Argon2PasswordHasher
from aether.adapters.clock import SystemClock
from aether.adapters.idgen import Uuid7Generator
from aether.adapters.jwt.eddsa import EdDSATokenSigner
from aether.adapters.postgres.pool import create_pool
from aether.adapters.postgres.refresh_token_repository import PostgresRefreshTokenRepository
from aether.adapters.postgres.user_repository import PostgresUserRepository
from aether.adapters.redis.denylist import RedisJtiDenylist
from aether.app.auth.login_user import LoginUser
from aether.app.auth.logout_user import LogoutUser
from aether.app.auth.refresh_session import RefreshSession
from aether.app.auth.register_user import RegisterUser
from aether.app.auth.revoke_user_sessions import RevokeUserSessions
from aether.config import Settings
from aether.ports.repositories import RefreshTokenRepositoryPort, UserRepositoryPort
from aether.ports.revocation import RevocationPort
from aether.ports.security import ClockPort, IdPort, PasswordHasherPort, TokenPort


@dataclass
class Container:
    db_pool: asyncpg.Pool
    redis_client: redis_asyncio.Redis

    users: UserRepositoryPort
    refresh_tokens: RefreshTokenRepositoryPort
    hasher: PasswordHasherPort
    tokens: TokenPort
    clock: ClockPort
    ids: IdPort
    revocations: RevocationPort

    register_user: RegisterUser
    login_user: LoginUser
    refresh_session: RefreshSession
    logout_user: LogoutUser
    revoke_user_sessions: RevokeUserSessions

    refresh_ttl_seconds: int

    async def aclose(self) -> None:
        await self.db_pool.close()
        await self.redis_client.aclose()


async def build_container(settings: Settings) -> Container:
    db_pool = await create_pool(settings.database_url)
    redis_client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]  # redis-py gap, not ours
        settings.redis_url, decode_responses=True
    )

    users = PostgresUserRepository(db_pool)
    refresh_tokens = PostgresRefreshTokenRepository(db_pool)
    hasher = Argon2PasswordHasher()
    tokens = EdDSATokenSigner(
        signing_key_b64=settings.jwt_signing_key,
        kid=settings.jwt_kid,
        access_ttl_seconds=settings.jwt_access_ttl_seconds,
    )
    clock = SystemClock()
    ids = Uuid7Generator()
    revocations = RedisJtiDenylist(redis_client)

    return Container(
        db_pool=db_pool,
        redis_client=redis_client,
        users=users,
        refresh_tokens=refresh_tokens,
        hasher=hasher,
        tokens=tokens,
        clock=clock,
        ids=ids,
        revocations=revocations,
        register_user=RegisterUser(users=users, hasher=hasher, ids=ids),
        login_user=LoginUser(
            users=users,
            refresh_tokens=refresh_tokens,
            hasher=hasher,
            tokens=tokens,
            clock=clock,
            ids=ids,
            refresh_ttl_seconds=settings.jwt_refresh_ttl_seconds,
        ),
        refresh_session=RefreshSession(
            refresh_tokens=refresh_tokens,
            tokens=tokens,
            clock=clock,
            ids=ids,
            refresh_ttl_seconds=settings.jwt_refresh_ttl_seconds,
            grace_seconds=settings.jwt_refresh_grace_seconds,
        ),
        logout_user=LogoutUser(refresh_tokens=refresh_tokens, revocations=revocations, clock=clock),
        revoke_user_sessions=RevokeUserSessions(refresh_tokens=refresh_tokens, clock=clock),
        refresh_ttl_seconds=settings.jwt_refresh_ttl_seconds,
    )

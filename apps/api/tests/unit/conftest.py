"""Fixtures for HTTP-layer unit tests: a real FastAPI app with get_container
overridden to return an all-fake Container — no real DB/Redis, no lifespan
needed, but exercises real routes/middleware/exception-handling."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aether.app.auth.login_user import LoginUser
from aether.app.auth.logout_user import LogoutUser
from aether.app.auth.refresh_session import RefreshSession
from aether.app.auth.register_user import RegisterUser
from aether.app.auth.revoke_user_sessions import RevokeUserSessions
from aether.http.app import create_app
from aether.http.deps import get_container
from tests.unit.fakes.auth import (
    FakeClock,
    FakeIdGenerator,
    FakePasswordHasher,
    FakeRefreshTokenRepository,
    FakeRevocationPort,
    FakeTokenPort,
    FakeUserRepository,
)
from tests.unit.fakes.workspaces import FakeAuditLog

_REFRESH_TTL = 604_800
_GRACE = 30


class FakeContainer:
    """Duck-types http.composition.Container closely enough for the routes
    under test — db_pool/redis_client are never touched because the
    lifespan never runs in these tests (get_container is overridden)."""

    def __init__(self) -> None:
        clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC))
        ids = FakeIdGenerator()
        users = FakeUserRepository()
        refresh_tokens = FakeRefreshTokenRepository()
        hasher = FakePasswordHasher()
        tokens = FakeTokenPort(clock=clock)
        revocations = FakeRevocationPort()
        audit_log = FakeAuditLog()

        self.users = users
        self.refresh_tokens = refresh_tokens
        self.hasher = hasher
        self.tokens = tokens
        self.clock = clock
        self.ids = ids
        self.revocations = revocations
        self.audit_log = audit_log
        self.refresh_ttl_seconds = _REFRESH_TTL

        self.register_user = RegisterUser(users=users, hasher=hasher, audit_log=audit_log, ids=ids)
        self.login_user = LoginUser(
            users=users,
            refresh_tokens=refresh_tokens,
            hasher=hasher,
            tokens=tokens,
            clock=clock,
            ids=ids,
            audit_log=audit_log,
            refresh_ttl_seconds=_REFRESH_TTL,
        )
        self.refresh_session = RefreshSession(
            refresh_tokens=refresh_tokens,
            tokens=tokens,
            clock=clock,
            ids=ids,
            refresh_ttl_seconds=_REFRESH_TTL,
            grace_seconds=_GRACE,
        )
        self.logout_user = LogoutUser(
            refresh_tokens=refresh_tokens,
            revocations=revocations,
            clock=clock,
            audit_log=audit_log,
            ids=ids,
        )
        self.revoke_user_sessions = RevokeUserSessions(refresh_tokens=refresh_tokens, clock=clock)


@pytest.fixture()
def fake_container() -> FakeContainer:
    return FakeContainer()


@pytest.fixture()
def app_with_fakes(fake_container: FakeContainer) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_container] = lambda: fake_container
    return app


@pytest.fixture()
def http_client(app_with_fakes: FastAPI) -> TestClient:
    # https:// base_url, not the default http://testserver: the refresh
    # cookie is Secure (ADR-7.1), and httpx's cookie jar — correctly —
    # won't send a Secure cookie back over a plain http:// connection.
    return TestClient(app_with_fakes, base_url="https://testserver")

"""Real-Postgres proof for API-key persistence (S10 #105, FR-API-2, §7.4)
— runs as app_api, the same role the running HTTP process uses.

api_keys is RLS-exempt (see its migration), so unlike most repository
integration tests here there's no cross-tenant-RLS scenario to prove —
what actually needs a real database is the ``touch_last_used`` hourly
coarsening (a real ``INTERVAL '1 hour'`` WHERE-clause comparison, not
something a fake could meaningfully stand in for) and the real UNIQUE
constraint on ``prefix``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from aether.adapters.postgres.api_key_repository import PostgresApiKeyRepository
from aether.domain.entities import ApiKeyScope

pytestmark = pytest.mark.integration


async def _seed_workspace_and_user(bootstrap_pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with bootstrap_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workspaces (id, name, slug) VALUES ($1, 'API Key Test', $2)",
            workspace_id,
            f"api-key-test-{workspace_id}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, display_name) VALUES ($1, $2, 'Test User')",
            user_id,
            f"{user_id}@example.com",
        )
    return workspace_id, user_id


async def test_create_get_by_prefix_and_list_by_workspace_round_trip(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, user_id = await _seed_workspace_and_user(db_bootstrap_pool)
    repo = PostgresApiKeyRepository(db_pool)

    created = await repo.create(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        prefix="abcd1234",
        secret_hash="hash-value",
        name="CI bot",
        scopes=frozenset({ApiKeyScope.CHAT_WRITE, ApiKeyScope.KB_READ}),
        created_by=user_id,
        expires_at=None,
    )

    assert created.prefix == "abcd1234"
    assert created.scopes == frozenset({ApiKeyScope.CHAT_WRITE, ApiKeyScope.KB_READ})

    fetched = await repo.get_by_prefix("abcd1234")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.secret_hash == "hash-value"

    listed = await repo.list_by_workspace(workspace_id)
    assert [k.id for k in listed] == [created.id]


async def test_get_by_prefix_returns_none_for_unknown_prefix(db_pool: asyncpg.Pool) -> None:
    repo = PostgresApiKeyRepository(db_pool)
    assert await repo.get_by_prefix("nonexist") is None


async def test_prefix_is_unique(db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool) -> None:
    workspace_id, user_id = await _seed_workspace_and_user(db_bootstrap_pool)
    repo = PostgresApiKeyRepository(db_pool)
    await repo.create(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        prefix="dupeprfx",
        secret_hash="hash-1",
        name="key-a",
        scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
        created_by=user_id,
        expires_at=None,
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            prefix="dupeprfx",
            secret_hash="hash-2",
            name="key-b",
            scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
            created_by=user_id,
            expires_at=None,
        )


async def test_revoke_only_matches_its_own_workspace(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_a, user_a = await _seed_workspace_and_user(db_bootstrap_pool)
    workspace_b, _ = await _seed_workspace_and_user(db_bootstrap_pool)
    repo = PostgresApiKeyRepository(db_pool)
    key = await repo.create(
        id=uuid.uuid4(),
        workspace_id=workspace_a,
        prefix="revoketst",
        secret_hash="hash",
        name="key-a",
        scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
        created_by=user_a,
        expires_at=None,
    )

    # A revoke call scoped to the *wrong* workspace must not touch it.
    await repo.revoke(workspace_b, key.id, revoked_at=datetime.now(UTC))
    unrevoked = await repo.get_by_prefix("revoketst")
    assert unrevoked is not None
    assert unrevoked.revoked_at is None

    await repo.revoke(workspace_a, key.id, revoked_at=datetime.now(UTC))
    revoked = await repo.get_by_prefix("revoketst")
    assert revoked is not None
    assert revoked.revoked_at is not None


async def test_touch_last_used_is_coarsened_to_hourly_granularity(
    db_pool: asyncpg.Pool, db_bootstrap_pool: asyncpg.Pool
) -> None:
    workspace_id, user_id = await _seed_workspace_and_user(db_bootstrap_pool)
    repo = PostgresApiKeyRepository(db_pool)
    key = await repo.create(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        prefix="touchtest",
        secret_hash="hash",
        name="key-a",
        scopes=frozenset({ApiKeyScope.CHAT_WRITE}),
        created_by=user_id,
        expires_at=None,
    )

    first_touch = datetime.now(UTC)
    await repo.touch_last_used(key.id, used_at=first_touch)
    after_first = await repo.get_by_prefix("touchtest")
    assert after_first is not None
    assert after_first.last_used_at is not None

    # A second touch a few minutes later, still within the same hour,
    # must be a no-op — the WHERE clause's real INTERVAL '1 hour'
    # comparison, only provable against a real database.
    second_touch = first_touch + timedelta(minutes=5)
    await repo.touch_last_used(key.id, used_at=second_touch)
    after_second = await repo.get_by_prefix("touchtest")
    assert after_second is not None
    assert after_second.last_used_at == after_first.last_used_at

    # A touch over an hour later does update.
    third_touch = first_touch + timedelta(hours=1, minutes=1)
    await repo.touch_last_used(key.id, used_at=third_touch)
    after_third = await repo.get_by_prefix("touchtest")
    assert after_third is not None
    assert after_third.last_used_at == third_touch

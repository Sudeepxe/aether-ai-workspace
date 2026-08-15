"""Integration test fixtures — real Postgres + Redis via testcontainers.

Local dev on Colima (macOS) needs ``TESTCONTAINERS_RYUK_DISABLED=true`` in
the environment — Colima's Docker socket mount trips up testcontainers'
reaper container. CI (standard Docker on GitHub Actions runners) needs no
such workaround and runs with the reaper enabled.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

API_DIR = Path(__file__).resolve().parents[2]

PG_IMAGE = (
    "pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b"
)
REDIS_IMAGE = (
    "redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2"
)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A fresh Postgres container, migrated once per test session."""
    with PostgresContainer(PG_IMAGE, driver=None) as pg:
        bootstrap_url = pg.get_connection_url()
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_DIR,
            env={**os.environ, "AETHER_DATABASE_MIGRATOR_URL": bootstrap_url},
            check=True,
        )
        yield bootstrap_url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(REDIS_IMAGE) as rd:
        host = rd.get_container_host_ip()
        port = rd.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture()
async def db_pool(postgres_url: str) -> AsyncIterator[asyncpg.Pool]:
    """A pool connected as app_api — the same least-privileged, RLS-subject
    role the running application uses. app_api deliberately has no TRUNCATE
    grant (it doesn't need one in production), so cleanup between tests is
    done by db_bootstrap_pool's teardown instead, not this fixture's."""
    app_api_url = _as_role(postgres_url, "app_api", "app-api-dev-only")
    pool = await asyncpg.create_pool(app_api_url, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
async def db_bootstrap_pool(postgres_url: str) -> AsyncIterator[asyncpg.Pool]:
    """A pool connected as the bootstrap (superuser-ish) role — used to seed
    or verify fixture data by bypassing RLS, and to truncate between tests
    (app_api has no TRUNCATE grant, matching its production privileges)."""
    pool = await asyncpg.create_pool(postgres_url, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE memberships, workspaces, users, refresh_tokens RESTART IDENTITY CASCADE"
            )
        await pool.close()


@pytest.fixture()
async def redis_client(redis_url: str) -> AsyncIterator[redis_asyncio.Redis]:
    client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]  # redis-py gap, not ours
        redis_url, decode_responses=True
    )
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


def _as_role(bootstrap_url: str, role: str, password: str) -> str:
    """Swap the bootstrap superuser credentials in a connection URL for a
    named application role's, keeping host/port/db unchanged."""
    # bootstrap_url looks like postgresql://test:test@localhost:PORT/test
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://{role}:{password}@{hostpart}"

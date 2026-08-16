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
import httpx
import pytest
import redis.asyncio as redis_asyncio
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from aether.adapters.minio.object_storage import MinioObjectStorage
from aether.adapters.postgres.pool import _init_connection

API_DIR = Path(__file__).resolve().parents[2]

PG_IMAGE = (
    "pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b"
)
REDIS_IMAGE = (
    "redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2"
)
# Same digest as infra/compose/compose.yml's mailpit service.
MAILPIT_IMAGE = (
    "axllent/mailpit:v1.20@sha256:7eef0f38dbc85e4e264f2edf5d70fbb694826791e05cb2cae4fc9e3282f968f5"
)
# Same digest as infra/compose/compose.yml's minio service.
MINIO_IMAGE = "minio/minio:RELEASE.2024-08-17T01-24-54Z@sha256:6f23072e3e222e64fe6f86b31a7f7aca971e5129e55cbccef649b109b8e651a1"


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
    pool = await asyncpg.create_pool(app_api_url, min_size=1, max_size=4, init=_init_connection)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
async def db_bootstrap_pool(postgres_url: str) -> AsyncIterator[asyncpg.Pool]:
    """A pool connected as the bootstrap (superuser-ish) role — used to seed
    or verify fixture data by bypassing RLS, and to truncate between tests
    (app_api has no TRUNCATE grant, matching its production privileges)."""
    pool = await asyncpg.create_pool(postgres_url, min_size=1, max_size=2, init=_init_connection)
    try:
        yield pool
    finally:
        async with pool.acquire() as conn:
            # TRUNCATE on a partitioned table (audit_events) cascades to
            # all its partitions automatically — no need to list them.
            await conn.execute(
                "TRUNCATE memberships, invitations, audit_events, outbox, "
                "password_reset_tokens, workspaces, users, refresh_tokens RESTART IDENTITY CASCADE"
            )
            # global_usage_counter has no FK relationship to workspaces
            # (deliberately — it's not tenant data, see its migration),
            # so TRUNCATE ... CASCADE above never reaches it; reset its
            # one row directly instead of truncating (a CHECK(id)
            # singleton table must never end up with zero rows).
            await conn.execute("UPDATE global_usage_counter SET settled_microcents = 0")
        await pool.close()


@pytest.fixture(scope="session")
def mailpit() -> Iterator[tuple[str, int, str]]:
    """Real mailpit — SMTP intake + HTTP API to assert on received mail.
    Yields (smtp_host, smtp_port, http_base_url)."""
    with DockerContainer(MAILPIT_IMAGE).with_exposed_ports(1025, 8025) as container:
        wait_for_logs(container, "accessible via", timeout=15)
        host = container.get_container_host_ip()
        smtp_port = int(container.get_exposed_port(1025))
        http_port = int(container.get_exposed_port(8025))
        yield host, smtp_port, f"http://{host}:{http_port}"


@pytest.fixture()
def mailpit_client(mailpit: tuple[str, int, str]) -> Iterator[httpx.Client]:
    _, _, base_url = mailpit
    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        client.delete("/api/v1/messages")  # start each test with an empty mailbox
        yield client


@pytest.fixture(scope="session")
def minio_endpoint() -> Iterator[tuple[str, str, str]]:
    """Real MinIO — yields (host:port, access_key, secret_key), same
    credential shape as infra/compose/compose.yml's dev service."""
    with MinioContainer(
        MINIO_IMAGE, access_key="aether", secret_key="aether-dev-only"
    ) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(container.port)
        yield f"{host}:{port}", "aether", "aether-dev-only"


@pytest.fixture()
async def object_storage(minio_endpoint: tuple[str, str, str]) -> MinioObjectStorage:
    """A fresh adapter per test, all sharing one session-scoped bucket —
    tests use unique (uuid-based) keys, so no cross-test cleanup is
    needed the way TRUNCATE handles Postgres state between tests."""
    endpoint, access_key, secret_key = minio_endpoint
    storage = MinioObjectStorage(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
        bucket="aether-test-documents",
    )
    await storage.ensure_bucket()
    return storage


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

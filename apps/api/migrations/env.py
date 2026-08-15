"""Alembic environment — migration runner only, no ORM (Blueprint ADR-8.5).

Every migration is hand-written raw SQL via ``op.execute(...)``; there is
no ``target_metadata`` and autogenerate is never used, because there is no
ORM model layer to diff against (approved Sprint 1 decision: asyncpg +
hand-written SQL in application code). Alembic's own dependency on
SQLAlchemy Core (the async engine below) is the migration tool's internal
implementation, not something application code ever imports.

The connection string is read from ``aether.config.Settings`` — one source
of truth, shared with the running application — rather than duplicated in
``alembic.ini``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from aether.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # no ORM models to autogenerate against — by design


def _asyncpg_url() -> str:
    """Migrations connect via database_migrator_url (bootstrap-privileged),
    never database_url (the least-privileged app_api role the running app
    uses) — a migration needs DDL rights app_api deliberately doesn't have.
    Settings uses the asyncpg-native `postgresql://` scheme; SQLAlchemy's
    async engine needs the `+asyncpg` suffix to route to the same driver."""
    url = get_settings().database_migrator_url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (`alembic upgrade --sql`)."""
    context.configure(
        url=_asyncpg_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live DB using an async engine (the normal path)."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _asyncpg_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

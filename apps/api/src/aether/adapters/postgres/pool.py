"""asyncpg pool factory. Raw SQL only — no ORM (approved Sprint 1 decision)."""

from __future__ import annotations

import json

import asyncpg


async def _init_connection(conn: asyncpg.Connection) -> None:
    # asyncpg returns jsonb as raw text by default; without this codec
    # every repository touching a jsonb column (workspaces.settings,
    # audit_events.metadata, ...) would have to json.loads/dumps by hand
    # at every call site instead of working with plain dicts.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10, init=_init_connection)
    if pool is None:  # pragma: no cover — asyncpg only returns None if closed mid-create
        raise RuntimeError("failed to create database pool")
    return pool

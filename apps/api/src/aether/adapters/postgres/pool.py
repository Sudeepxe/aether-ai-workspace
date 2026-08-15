"""asyncpg pool factory. Raw SQL only — no ORM (approved Sprint 1 decision)."""

from __future__ import annotations

import asyncpg


async def create_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
    if pool is None:  # pragma: no cover — asyncpg only returns None if closed mid-create
        raise RuntimeError("failed to create database pool")
    return pool

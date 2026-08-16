"""Postgres-backed BudgetRepositoryPort implementation.

Connection-bound (see workspace_repository.py's docstring) — used by
WorkspaceScope for the ordinary GET/PUT /budget HTTP endpoints.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import asyncpg

from aether.ports.metering import Budget


class PostgresBudgetRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get(self, workspace_id: UUID) -> Budget | None:
        row = await self._conn.fetchrow(
            "SELECT workspace_id, monthly_limit_microcents, soft_pct, current_period_start, "
            "settled_microcents, updated_at FROM budgets WHERE workspace_id = $1",
            workspace_id,
        )
        return _row_to_budget(row) if row is not None else None

    async def create(
        self,
        *,
        workspace_id: UUID,
        monthly_limit_microcents: int,
        soft_pct: int,
        current_period_start: date,
    ) -> Budget:
        row = await self._conn.fetchrow(
            """
            INSERT INTO budgets (workspace_id, monthly_limit_microcents, soft_pct, current_period_start)
            VALUES ($1, $2, $3, $4)
            RETURNING workspace_id, monthly_limit_microcents, soft_pct, current_period_start,
                      settled_microcents, updated_at
            """,
            workspace_id,
            monthly_limit_microcents,
            soft_pct,
            current_period_start,
        )
        assert row is not None  # noqa: S101 — INSERT ... RETURNING always yields a row on success
        return _row_to_budget(row)

    async def update_limit(
        self,
        workspace_id: UUID,
        *,
        monthly_limit_microcents: int,
        expected_updated_at: datetime,
    ) -> Budget | None:
        row = await self._conn.fetchrow(
            """
            UPDATE budgets SET monthly_limit_microcents = $2, updated_at = now()
            WHERE workspace_id = $1 AND updated_at = $3
            RETURNING workspace_id, monthly_limit_microcents, soft_pct, current_period_start,
                      settled_microcents, updated_at
            """,
            workspace_id,
            monthly_limit_microcents,
            expected_updated_at,
        )
        return _row_to_budget(row) if row is not None else None


def _row_to_budget(row: asyncpg.Record) -> Budget:
    return Budget(
        workspace_id=row["workspace_id"],
        monthly_limit_microcents=row["monthly_limit_microcents"],
        soft_pct=row["soft_pct"],
        current_period_start=row["current_period_start"],
        settled_microcents=row["settled_microcents"],
        updated_at=row["updated_at"],
    )

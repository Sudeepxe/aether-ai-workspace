from __future__ import annotations

import asyncpg


class PostgresCostMetricsRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_global_spend_microcents(self) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT settled_microcents FROM global_usage_counter WHERE id = true"
            )
        return int(value)

"""Worker-process composition root — the worker's counterpart to
http/composition.py. Same discipline: this is the one place in the
worker process allowed to import adapters directly (Blueprint §3.3).
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from aether.adapters.clock import SystemClock
from aether.adapters.email.resend import ResendEmailAdapter
from aether.adapters.email.smtp import SmtpEmailAdapter
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.pool import create_pool
from aether.app.notifications.dispatch_email_outbox import DispatchEmailOutbox
from aether.config import Settings
from aether.ports.email import EmailPort
from aether.ports.outbox import OutboxRepositoryPort
from aether.ports.security import ClockPort


@dataclass
class WorkerContainer:
    db_pool: asyncpg.Pool
    outbox: OutboxRepositoryPort
    email: EmailPort
    clock: ClockPort
    dispatch_email_outbox: DispatchEmailOutbox

    async def aclose(self) -> None:
        await self.db_pool.close()


def _build_email_adapter(settings: Settings) -> EmailPort:
    if settings.email_provider == "resend":
        return ResendEmailAdapter(api_key=settings.resend_api_key, sender=settings.email_sender)
    return SmtpEmailAdapter(
        host=settings.smtp_host, port=settings.smtp_port, sender=settings.email_sender
    )


async def build_worker_container(settings: Settings) -> WorkerContainer:
    db_pool = await create_pool(settings.database_worker_url)
    outbox = PostgresOutboxRepository(db_pool)
    email = _build_email_adapter(settings)
    clock = SystemClock()

    return WorkerContainer(
        db_pool=db_pool,
        outbox=outbox,
        email=email,
        clock=clock,
        dispatch_email_outbox=DispatchEmailOutbox(outbox=outbox, email=email, clock=clock),
    )

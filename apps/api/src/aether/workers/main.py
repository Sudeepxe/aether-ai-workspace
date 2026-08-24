"""Worker entrypoint.

Sprint 0 shipped the process skeleton (start/log/graceful-shutdown, no
consumer). Sprint 2 adds the first real one: polling the transactional
outbox for pending emails (ADR-11.1). Sprint 5 adds the outbox->
ingestion-queue dispatcher (relaying document.uploaded rows into the
per-tenant fair-queued Redis Streams consumer group) and, once issue #46
built a real handler, the queue's own consumer loop — fetch -> malware
scan -> parse -> chunk, run as a background task alongside the polling
loop. Sprint 8 adds the workspace-deletion saga dispatcher (DF-3, issue
#84): outbox-driven, same poll-loop shape as the other two. Graceful
shutdown (§3.2.8: "finish or re-queue on SIGTERM, <=30s") shares the
same stop event with the poll loop: the consumer only checks it between
claims, so a message already in flight always finishes before this
process actually exits.
"""

from __future__ import annotations

import asyncio
import signal
from types import FrameType

from aether.app.ingestion.consume_queue import run_ingestion_consumer
from aether.config import get_settings
from aether.logging import configure_logging, get_logger
from aether.workers.composition import build_worker_container

log = get_logger(__name__)

_POLL_INTERVAL_SECONDS = 5.0
_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS = 30.0


async def _run_async() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, service_name="aether-worker")
    container = await build_worker_container(settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle(signum: int, _frame: FrameType | None = None) -> None:
        log.info("shutdown_signal", signal=signal.Signals(signum).name)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle, sig)

    consumer_task = asyncio.ensure_future(
        run_ingestion_consumer(container.ingestion_queue, container.process_document, stop=stop)
    )

    log.info("worker_started")
    try:
        while not stop.is_set():
            result = await container.dispatch_email_outbox.execute()
            if result.dispatched or result.failed:
                log.info(
                    "email_outbox_dispatch_cycle",
                    dispatched=result.dispatched,
                    failed=result.failed,
                )
            ingestion_result = await container.dispatch_ingestion_outbox.execute()
            if ingestion_result.dispatched or ingestion_result.failed:
                log.info(
                    "ingestion_outbox_dispatch_cycle",
                    dispatched=ingestion_result.dispatched,
                    failed=ingestion_result.failed,
                )
            deletion_result = await container.dispatch_workspace_deletion.execute()
            if deletion_result.dispatched or deletion_result.failed:
                log.info(
                    "workspace_deletion_dispatch_cycle",
                    dispatched=deletion_result.dispatched,
                    failed=deletion_result.failed,
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=_POLL_INTERVAL_SECONDS)
            except TimeoutError:
                pass  # normal: no shutdown signal within this poll interval
    finally:
        try:
            await asyncio.wait_for(consumer_task, timeout=_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS)
        except TimeoutError:
            log.error("ingestion_consumer_shutdown_timed_out")
            consumer_task.cancel()
        await container.aclose()
        log.info("worker_stopped")


def run() -> None:
    asyncio.run(_run_async())


if __name__ == "__main__":
    run()

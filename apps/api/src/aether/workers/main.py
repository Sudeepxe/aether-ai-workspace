"""Worker entrypoint.

Sprint 0 shipped the process skeleton (start/log/graceful-shutdown, no
consumer). Sprint 2 adds the first real one: polling the transactional
outbox for pending emails (ADR-11.1). The Redis-Streams-based queue
consumers for the conversation/ingestion planes are still correctly
deferred to S5 — this is a narrower, already-needed mechanism, not that
one arriving early.
"""

from __future__ import annotations

import asyncio
import signal
from types import FrameType

from aether.config import get_settings
from aether.logging import configure_logging, get_logger
from aether.workers.composition import build_worker_container

log = get_logger(__name__)

_POLL_INTERVAL_SECONDS = 5.0


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
            try:
                await asyncio.wait_for(stop.wait(), timeout=_POLL_INTERVAL_SECONDS)
            except TimeoutError:
                pass  # normal: no shutdown signal within this poll interval
    finally:
        await container.aclose()
        log.info("worker_stopped")


def run() -> None:
    asyncio.run(_run_async())


if __name__ == "__main__":
    run()

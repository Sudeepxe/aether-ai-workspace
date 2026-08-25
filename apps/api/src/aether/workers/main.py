"""Worker entrypoint.

Sprint 0 shipped the process skeleton (start/log/graceful-shutdown, no
consumer). Sprint 2 adds the first real one: polling the transactional
outbox for pending emails (ADR-11.1). Sprint 5 adds the outbox->
ingestion-queue dispatcher (relaying document.uploaded rows into the
per-tenant fair-queued Redis Streams consumer group) and, once issue #46
built a real handler, the queue's own consumer loop — fetch -> malware
scan -> parse -> chunk, run as a background task alongside the polling
loop. Sprint 8 adds the workspace-deletion (DF-3, #84) and workspace-
export (FR-AD-5, #85) saga dispatchers — outbox-driven, same poll-loop
shape as the other two — plus the deletion-verification sweep (NFR-PR-1,
#86), which is the one dispatcher here that *isn't* outbox-driven: it
polls completed deletion_jobs directly, since there's no natural event
to enqueue for "verify sometime after the fact". Graceful shutdown
(§3.2.8: "finish or re-queue on SIGTERM, <=30s") shares the
same stop event with the poll loop: the consumer only checks it between
claims, so a message already in flight always finishes before this
process actually exits.
"""

from __future__ import annotations

import asyncio
import signal
from types import FrameType

from prometheus_client import start_http_server

from aether.app.ingestion.consume_queue import run_ingestion_consumer
from aether.config import get_settings
from aether.logging import configure_logging, get_logger
from aether.observability.metrics import (
    GLOBAL_BUDGET_CAP_MICROCENTS,
    GLOBAL_SPEND_MICROCENTS,
    INGESTION_DLQ_DEPTH,
    INGESTION_PENDING_TENANTS,
    INGESTION_QUEUE_DEPTH,
    OUTBOX_DLQ_DEPTH,
    OUTBOX_LAG_SECONDS,
)
from aether.observability.tracing import configure_tracing, instrument_libraries
from aether.ports.cost_metrics import CostMetricsPort
from aether.ports.ingestion_queue_metrics import IngestionQueueMetricsPort
from aether.ports.outbox_metrics import OutboxMetricsPort
from aether.workers.composition import build_worker_container

log = get_logger(__name__)

_POLL_INTERVAL_SECONDS = 5.0
_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS = 30.0
# The four outbox-driven event types this worker dispatches (mirrors the
# poll loop below) — the deletion-verification sweep is deliberately
# excluded, since it isn't outbox-driven (see this module's own
# docstring) and so has no "pending" notion these gauges apply to.
_TRACKED_EVENT_TYPES = (
    "email.send",
    "document.uploaded",
    "workspace.delete_requested",
    "workspace.export_requested",
)
_MAX_DISPATCH_ATTEMPTS = 5


async def _record_outbox_gauges(outbox_metrics: OutboxMetricsPort) -> None:
    for event_type in _TRACKED_EVENT_TYPES:
        stats = await outbox_metrics.get_stats(
            event_type=event_type, max_attempts=_MAX_DISPATCH_ATTEMPTS
        )
        if stats.oldest_pending_seconds is not None:
            OUTBOX_LAG_SECONDS.labels(event_type=event_type).set(stats.oldest_pending_seconds)
        else:
            OUTBOX_LAG_SECONDS.labels(event_type=event_type).set(0)
        OUTBOX_DLQ_DEPTH.labels(event_type=event_type).set(stats.dlq_depth)


async def _record_ingestion_gauges(ingestion_queue_metrics: IngestionQueueMetricsPort) -> None:
    stats = await ingestion_queue_metrics.get_stats()
    INGESTION_QUEUE_DEPTH.set(stats.total_queued)
    INGESTION_PENDING_TENANTS.set(stats.pending_tenants)
    INGESTION_DLQ_DEPTH.set(stats.dlq_depth)


async def _record_cost_gauge(cost_metrics: CostMetricsPort) -> None:
    GLOBAL_SPEND_MICROCENTS.set(await cost_metrics.get_global_spend_microcents())


async def _run_async() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, service_name="aether-worker")
    configure_tracing(
        service_name="aether-worker",
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        sample_ratio=settings.otel_trace_sample_ratio,
        environment=settings.env,
    )
    instrument_libraries()
    start_http_server(settings.worker_metrics_port)
    GLOBAL_BUDGET_CAP_MICROCENTS.set(settings.global_monthly_budget_microcents)
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
            export_result = await container.dispatch_workspace_export.execute()
            if export_result.dispatched or export_result.failed:
                log.info(
                    "workspace_export_dispatch_cycle",
                    dispatched=export_result.dispatched,
                    failed=export_result.failed,
                )
            verification_result = await container.verify_workspace_deletions.execute()
            if verification_result.passed or verification_result.failed:
                log.info(
                    "deletion_verification_cycle",
                    passed=verification_result.passed,
                    failed=verification_result.failed,
                )
            await _record_outbox_gauges(container.outbox_metrics)
            await _record_ingestion_gauges(container.ingestion_queue_metrics)
            await _record_cost_gauge(container.cost_metrics)
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

"""NFR-O-1's own verifying mechanism ("trace assertion tests"): proves
one user action produces one *correlated* trace across the outbox's
async seam (producer enqueue -> worker dispatch), not just that spans
exist somewhere. Against a real Postgres-backed outbox — the trace
context genuinely round-trips through the ``trace_context`` jsonb
column (migration 8053de1b0539), not a fake's in-memory dict.

Installs a real ``TracerProvider`` backed by an in-memory exporter
directly, bypassing ``observability.tracing.configure_tracing``'s
"no-op unless an OTLP endpoint is configured" guard that every other
test in this suite relies on to avoid a real exporter's background
thread. This is deliberately the *only* place in the whole suite
allowed to call ``trace.set_tracer_provider()`` with a real provider —
the OTel API only honors the first such call per process, so this
module asserts it actually took effect rather than assuming it did.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aether.adapters.clock import SystemClock
from aether.adapters.postgres.outbox_repository import PostgresOutboxRepository
from aether.adapters.postgres.pool import create_pool
from aether.app.notifications.dispatch_email_outbox import (
    EMAIL_SEND_EVENT_TYPE,
    DispatchEmailOutbox,
)

pytestmark = pytest.mark.integration


def _as_role(bootstrap_url: str, role: str, password: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://{role}:{password}@{hostpart}"


@pytest.fixture(scope="module")
def span_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    assert isinstance(trace.get_tracer_provider(), TracerProvider), (
        "trace.set_tracer_provider() only honors the first real provider set in a "
        "process — some other test already claimed that slot, so this fixture's "
        "provider never took effect and this whole module's assertions would be "
        "meaningless. Fix: no other test module may call set_tracer_provider()."
    )
    return exporter


class _FakeEmail:
    async def send(self, message: object) -> None:  # pragma: no cover — trivial
        pass


async def test_a_single_outbox_row_carries_one_trace_id_from_enqueue_to_dispatch(
    postgres_url: str,
    span_exporter: InMemorySpanExporter,
) -> None:
    api_pool = await create_pool(_as_role(postgres_url, "app_api", "app-api-dev-only"))
    worker_pool = await create_pool(_as_role(postgres_url, "app_worker", "app-worker-dev-only"))
    tenant_id = uuid4()
    try:
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("simulated_http_request") as producer_span:
            producer_trace_id = producer_span.get_span_context().trace_id
            async with api_pool.acquire() as conn:
                # outbox is deliberately RLS-exempt (see the outbox
                # migration's docstring) — no app.tenant_id config needed
                # for this INSERT.
                await PostgresOutboxRepository(conn).enqueue(
                    id=uuid4(),
                    aggregate_type="user",
                    aggregate_id=tenant_id,
                    event_type=EMAIL_SEND_EVENT_TYPE,
                    tenant_id=tenant_id,
                    payload={
                        "to": "trace-assertion@example.com",
                        "subject": "trace assertion",
                        "text_body": "body",
                    },
                )

        dispatcher = DispatchEmailOutbox(
            outbox=PostgresOutboxRepository(worker_pool),
            email=_FakeEmail(),
            clock=SystemClock(),
        )
        result = await dispatcher.execute()
        assert result.dispatched == 1
    finally:
        await api_pool.close()
        await worker_pool.close()

    spans = span_exporter.get_finished_spans()
    dispatch_spans = [s for s in spans if s.name == "outbox.dispatch.email.send"]
    assert len(dispatch_spans) == 1, (
        f"expected exactly one worker-side dispatch span, got {len(dispatch_spans)}: "
        f"{[s.name for s in spans]}"
    )
    assert dispatch_spans[0].context.trace_id == producer_trace_id, (
        "the worker's dispatch span must share the API request's trace id — this is "
        "the whole point of NFR-O-1's 'one trace per user action across the async "
        "seam'; a mismatch means trace_context round-tripped through the outbox row "
        "incorrectly (or wasn't captured/extracted at all)"
    )

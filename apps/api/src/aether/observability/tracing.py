"""OpenTelemetry tracing setup (NFR-O-1, §3.8): W3C ``traceparent``
propagation API -> outbox -> queue -> worker, one trace per user action
across the async seam. Sampling: a ratio-based head sampler at the SDK
(configurable, ``otel_trace_sample_ratio``) plus tail sampling in the
otel-collector (100% of error traces, regardless of the head sampler's
decision — a head sampler alone cannot implement "100% on error" since
the decision is made before the request's outcome is known; see
``infra/otel-collector/config.yaml``).

Exporting is a no-op (``NoOpTracerProvider``, the OTel API's own default)
when no endpoint is configured — the same "falls back to an honest local
placeholder, no code change needed once configured" posture already used
for the LLM Router and embedder (D6-3): dev/CI without a running
collector just gets untraced spans instead of import errors or a hung
background exporter.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

_TRACER_NAME: Final[str] = "aether"
_configured = False


def configure_tracing(
    *, service_name: str, otlp_endpoint: str, sample_ratio: float, environment: str
) -> None:
    """Idempotent: safe to call more than once per process (tests build
    multiple containers) — only the first call actually installs a
    provider, matching ``configure_logging``'s process-wide-singleton
    posture."""
    global _configured
    if _configured or not otlp_endpoint:
        return
    provider = TracerProvider(
        resource=Resource.create(
            {SERVICE_NAME: service_name, "deployment.environment": environment}
        ),
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _configured = True


def instrument_libraries() -> None:
    """asyncpg/httpx/redis auto-instrumentation — DB queries, provider
    calls (OpenAI/Anthropic/Resend all go through httpx), and Redis
    Streams/rate-limit calls all become child spans automatically.
    Idempotent: ``BaseInstrumentor.instrument()`` no-ops (logs a
    warning) on a library already instrumented, which is what lets both
    ``create_app()`` and ``build_worker_container()`` call this
    unconditionally even though tests build many containers per
    process."""
    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]  # opentelemetry-instrumentation-asyncpg ships no stubs
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def current_trace_id() -> str | None:
    """The active span's trace id as a lowercase hex string, or ``None``
    when there is no recording span (tracing unconfigured, or the call
    happens outside any span) — the caller falls back to a minted
    correlation id in that case."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


def inject_trace_context() -> dict[str, str]:
    """Capture the current span's context into a carrier dict, for
    storage alongside an outbox row (or any other durable handoff point)
    so a worker dispatching that row later can resume the same trace."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def linked_span(name: str, carrier: dict[str, str] | None) -> Iterator[None]:
    """Start a span parented to a previously-captured carrier (typically
    an outbox row's ``trace_context``) — the worker-side half of
    ``inject_trace_context``. A missing/empty carrier (e.g. a row
    written before this feature existed, or tracing was unconfigured at
    enqueue time) degrades to an uncorrelated span rather than an error."""
    ctx = propagate.extract(carrier) if carrier else None
    tracer = get_tracer()
    with tracer.start_as_current_span(name, context=ctx):
        yield

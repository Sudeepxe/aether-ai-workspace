"""Prometheus metrics (§10.4's dashboards feed off these): request
latency histograms (NFR-P-1/2/3 SLO tracking), outbox lag/DLQ depth
(worker poll loop), and LLM provider TTFT/fallback counters (§6.7's
AI-plane dashboard). Metric objects are module-level singletons —
defined once at import time regardless of how many times
``create_app()``/``build_worker_container()`` run (tests build several
per process), avoiding prometheus_client's "duplicated timeseries"
error on re-registration.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "aether_http_request_duration_seconds",
    "HTTP server request duration in seconds, by route (NFR-P-1/2/3)",
    labelnames=("method", "route", "status"),
)

OUTBOX_LAG_SECONDS = Gauge(
    "aether_outbox_lag_seconds",
    "Age of the oldest undispatched outbox row, by event type (page-grade alert at >5min)",
    labelnames=("event_type",),
)

OUTBOX_DLQ_DEPTH = Gauge(
    "aether_outbox_dlq_depth",
    "Outbox rows that exhausted their dispatch attempts, by event type (page-grade alert at >0, 15min sustained)",
    labelnames=("event_type",),
)

LLM_PROVIDER_REQUEST_DURATION_SECONDS = Histogram(
    "aether_llm_provider_ttft_seconds",
    "Time to first streamed token/usage chunk from an LLM provider, by provider/model/outcome",
    labelnames=("provider", "model", "outcome"),
)

LLM_PROVIDER_FALLBACK_TOTAL = Counter(
    "aether_llm_provider_fallback_total",
    "LLM Router fallbacks away from a provider, by provider and reason",
    labelnames=("provider", "reason"),
)


def metrics_endpoint() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def http_metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    # Starlette populates scope["route"] once dispatch resolves a match
    # (true even through FastAPI's include_router() nesting) — read
    # *after* call_next so the path *template* (e.g.
    # "/v1/threads/{thread_id}") is available; a route that never
    # matched (404) falls back to "unmatched" rather than the raw path,
    # since raw paths would make every distinct UUID its own timeseries
    # (unbounded cardinality).
    route = request.scope.get("route")
    route_template = getattr(route, "path", None) or "unmatched"
    HTTP_REQUEST_DURATION_SECONDS.labels(
        method=request.method, route=route_template, status=str(response.status_code)
    ).observe(time.perf_counter() - start)
    return response

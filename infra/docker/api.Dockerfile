# syntax=docker/dockerfile:1.7
# Aether API/worker image — one image, two entrypoints (Blueprint D3-1, §3.9.1).
# Multi-stage: uv builder -> slim non-root runtime.
# NOTE(S0): base images pinned by tag; digest pinning is a tracked Sprint 0
# follow-up (issue: "Pin base images by digest") per Ch. 10 F-1 discipline.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.4.18 /uv /usr/local/bin/uv
WORKDIR /build
COPY apps/api/pyproject.toml apps/api/uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project || uv sync --no-dev --no-install-project --no-cache
COPY apps/api/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev

FROM python:3.12-slim AS runtime
RUN groupadd -r aether && useradd -r -g aether -u 10001 aether \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/src /app/src
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src PYTHONUNBUFFERED=1
USER aether
# Liveness endpoint only — readiness is checked by the orchestrator (§3.9.1).
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1
# API entrypoint; worker containers override CMD with aether.workers.main.
CMD ["python", "-m", "aether.http.main"]

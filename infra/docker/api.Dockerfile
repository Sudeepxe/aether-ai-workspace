# syntax=docker/dockerfile:1.7
# Aether API/worker image — one image, two entrypoints (Blueprint D3-1, §3.9.1).
# Multi-stage: uv builder -> slim non-root runtime.
# Base images pinned by digest, in `image:tag@digest` form so the tag
# stays human-readable next to the immutable pin (Ch. 10 F-1 discipline:
# no `latest`, no mutable tags). Digests captured 2026-08-14.

FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.4.18@sha256:7091de9df72a77bdc92d6460f09403b4bdd5b35fe54e3320e4e1cbdacf8cdd49 /uv /usr/local/bin/uv
WORKDIR /build
# README.md must be present before the first `uv sync`: pyproject.toml
# declares it as the package readme, and hatchling validates the file
# exists while resolving project metadata, even with --no-install-project.
COPY apps/api/pyproject.toml apps/api/uv.lock* apps/api/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project || uv sync --no-dev --no-install-project --no-cache
COPY apps/api/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev

FROM python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65 AS runtime
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

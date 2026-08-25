# Aether AI Workspace — developer interface (SPRINT_0_PLAN §15).
# Every CI job has a make twin; CI is the authority, make is the convenience.

SHELL := /bin/bash
.DEFAULT_GOAL := help

API_DIR := apps/api
WEB_DIR := apps/web
COMPOSE := docker compose -f infra/compose/compose.yml

.PHONY: help bootstrap dev dev-observability down down-observability lint typecheck test build clean env-check secrets-edit secrets-env minio-setup

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Full dev setup: toolchain check, deps, hooks, infra pull (target ≤15 min, NFR-M-1)
	@command -v docker >/dev/null || (echo "ERROR: docker required" && exit 1)
	@command -v uv >/dev/null || (echo "ERROR: uv required (https://docs.astral.sh/uv/)" && exit 1)
	@command -v node >/dev/null || (echo "ERROR: node LTS required" && exit 1)
	cd $(API_DIR) && uv sync
	cd $(WEB_DIR) && npm ci || npm install
	uv tool install pre-commit || true
	pre-commit install || true
	$(COMPOSE) --profile dev pull
	$(MAKE) env-check
	@echo "bootstrap complete — run 'make dev'"

dev: ## Start dev infra services (PG, Redis, MinIO, mailpit)
	$(COMPOSE) --profile dev up -d --wait
	$(MAKE) minio-setup
	@echo "infra up — api: cd $(API_DIR) && uv run python -m aether.http.main"

down: ## Stop dev infra
	$(COMPOSE) --profile dev down

dev-observability: ## Start the LGTM stack (Prometheus/Loki/Tempo/Grafana + otel-collector + Alertmanager, S9 §3.8)
	$(COMPOSE) --profile observability up -d --wait
	@echo "grafana: http://localhost:3000 (anonymous admin, dev-only) — set AETHER_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 to start exporting traces"

down-observability: ## Stop the LGTM stack
	$(COMPOSE) --profile observability down

minio-setup: ## Idempotently ensure the dev document-storage bucket exists (S5, ADR-3.8)
	cd $(API_DIR) && uv run python -c "\
import asyncio; \
from aether.adapters.minio.object_storage import MinioObjectStorage; \
from aether.config import get_settings; \
s = get_settings(); \
asyncio.run(MinioObjectStorage(endpoint=s.object_storage_endpoint, access_key=s.object_storage_access_key, secret_key=s.object_storage_secret_key, secure=s.object_storage_secure, bucket=s.object_storage_bucket).ensure_bucket())"

lint: env-check ## Ruff + import boundaries + eslint
	cd $(API_DIR) && uv run ruff format --check . && uv run ruff check . \
		&& uv run lint-imports --config pyproject.toml
	cd $(WEB_DIR) && npm run lint

typecheck: ## mypy --strict + tsc
	cd $(API_DIR) && uv run mypy
	cd $(WEB_DIR) && npm run typecheck

test: ## Python unit + architecture tests (integration lanes enable S1+)
	cd $(API_DIR) && uv run pytest -m "unit or architecture" --cov

test-integration: ## Real PG/Redis via testcontainers: integration + security marks (S1+)
	cd $(API_DIR) && uv run pytest -m "integration or security" --cov --cov-append

migrate: ## Apply Alembic migrations (needs AETHER_DATABASE_MIGRATOR_URL reachable)
	cd $(API_DIR) && uv run alembic upgrade head

build: ## Build container images
	docker build -f infra/docker/api.Dockerfile -t aether-api:local .
	docker build -f infra/docker/web.Dockerfile -t aether-web:local .

env-check: ## Fail if .env has keys missing from .env.example (drift guard)
	@if [ -f .env ]; then \
		missing=$$(comm -23 <(grep -oE '^[A-Z_]+' .env | sort -u) <(grep -oE '^[A-Z_]+' .env.example | sort -u)); \
		if [ -n "$$missing" ]; then echo "ERROR: .env keys not in .env.example: $$missing"; exit 1; fi; \
	fi

secrets-edit: ## Edit encrypted secrets bundle (SOPS + age, ADR-7.5)
	sops infra/secrets/dev.enc.yaml

secrets-env: ## Print `export KEY=value` lines decrypted from the secrets bundle — eval this into your shell, never pipe its output anywhere logged
	@sops -d --output-type dotenv --input-type yaml infra/secrets/dev.enc.yaml | grep -v '^#' | sed 's/^/export /'

clean: ## Remove caches and build artifacts
	rm -rf $(API_DIR)/.venv $(API_DIR)/.mypy_cache $(API_DIR)/.ruff_cache $(API_DIR)/.pytest_cache
	rm -rf $(WEB_DIR)/node_modules $(WEB_DIR)/dist

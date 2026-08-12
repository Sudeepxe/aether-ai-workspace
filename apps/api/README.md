# aether-api

The Aether AI Workspace backend: **one codebase, two entrypoints** (Blueprint
D3-1) — `aether.http.main` (API) and `aether.workers.main` (worker).

## Layout (hexagonal, lint-enforced — Blueprint §3.3)

| Package | Rule |
|---|---|
| `aether.domain` | Pure. Imports no other internal layer. |
| `aether.app` | Use cases. Imports domain + ports only. |
| `aether.ports` | Interfaces owned by the core. |
| `aether.adapters` | Implement ports. Import ports only. |
| `aether.modules.*` | 1:1 with the Blueprint §3.2 service catalog. |
| `aether.http` / `aether.workers` | Inbound adapters / entrypoints. |

Boundaries are enforced by **import-linter** (config in `pyproject.toml`)
in three places: pre-commit, the CI lint lane, and
`tests/architecture/test_import_boundaries.py`.

## Sprint 0 state

Only bootstrap wiring exists: settings, JSON logging with correlation IDs,
`/healthz` + `/readyz`, and a signal-aware worker skeleton. No business
logic, by design. See `SPRINT_0_PLAN.md` and the repo-root README.

## Commands

From the repo root: `make lint · make typecheck · make test · make dev`.
Locally within this app: `uv sync && uv run pytest`.

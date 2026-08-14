# ADR-8.1: Shared tables with forced row-level security and three distinct database roles (D8-1)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The multi-tenancy model needed a data-isolation strategy decision balancing operational simplicity against isolation strength, given the scale envelope (100 tenants, growing to 10,000 at 100x) and single-engineer operational capacity.

## Decision

Shared tables with a tenant_id (equal to workspace_id) column on every tenant-scoped table, with PostgreSQL row-level security as the enforcement backstop, and three distinct runtime database roles (app_api, app_worker, app_migrator) with distinct grants. Row-level security is enabled and forced even for table owners.

## Alternatives considered

- **Schema-per-tenant** — at the 10,000-tenant 100x envelope this makes migrations an O(tenants) operation and fragments connection pools with documented catalog-bloat pain.

- **Database-per-tenant** — operationally absurd for a self-serve platform at this team size, though recorded as the eventual answer for a dedicated-tenant enterprise tier.

- **Citus or other sharding extensions** — machinery for a scale problem the envelope doesn't have, though the tenant_id-everywhere discipline is specifically what would make Citus adoptable later without rework.

## Consequences

Easier: one schema to migrate and operate regardless of tenant count at v1 scale; forcing row-level security even for owners closes the classic superuser-bypass habit in review rather than in production. Harder: a single shared Postgres invites noisy-neighbor effects across tenants, mitigated elsewhere via per-tenant rate limits, statement timeouts, and PgBouncer.

## Revisit trigger

A dedicated-tenant enterprise tier is needed.

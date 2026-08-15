"""Postgres repository adapters — connect as app_api (ADR-8.1), never a
superuser. RLS-aware repositories (tenant-scoped tables) set the tenant
context via ``SELECT set_config('app.tenant_id', $1, true)`` per
transaction; land alongside the first tenant-scoped repository (S2+).
"""

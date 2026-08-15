"""Application settings — environment-driven, 12-factor (Blueprint §3.9.2).

Every variable read here must also appear in the repo-root ``.env.example``
(drift guard in ``make lint``, SPRINT_0_PLAN §15). Dev-safe defaults follow
the same posture as the existing ``POSTGRES_PASSWORD``/``MINIO_ROOT_PASSWORD``
values: committed, non-secret, dev-profile-only — real values come from the
SOPS bundle (ADR-7.5), never from a committed ``.env``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level configuration, injected via environment."""

    model_config = SettingsConfigDict(env_prefix="AETHER_", frozen=True)

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    service_name: str = "aether-api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- data layer (Sprint 1) -----------------------------------------
    # The running API/worker connect as the least-privileged `app_api`
    # role (ADR-8.1) — subject to RLS like any other application code, not
    # exempt from it. Migrations connect separately (database_migrator_url)
    # using the bootstrap role that has DDL rights, since app_migrator
    # doesn't yet have its own deploy-pipeline connection path (that's an
    # S11 concern) — see migrations/env.py.
    database_url: str = "postgresql://app_api:app-api-dev-only@localhost:5432/aether"
    database_migrator_url: str = "postgresql://aether:aether-dev-only@localhost:5432/aether"
    # The worker process (Sprint 2: outbox dispatch) connects as the
    # separate, least-privileged app_worker role — its grants (outbox
    # SELECT/UPDATE) are disjoint from app_api's, matching ADR-8.1's
    # three-role model.
    database_worker_url: str = "postgresql://app_worker:app-worker-dev-only@localhost:5432/aether"
    redis_url: str = "redis://localhost:6379/0"

    # --- auth / JWT (Sprint 1, ADR-7.2) ---------------------------------
    # Base64-encoded raw Ed25519 seed. The value below is a dev-only key,
    # generated for this scaffold and safe to commit (mirrors the existing
    # dev-default posture); it signs nothing that matters outside a local
    # `dev` compose profile. Prod overrides this via the SOPS bundle.
    jwt_signing_key: str = "ENa+PofIf23y5gFynYezonUkV5iu0pgeEe/PHlqCG4E="
    jwt_kid: str = "dev-1"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 604_800
    jwt_refresh_grace_seconds: int = 30

    # --- email (Sprint 2, ADR-11.1) --------------------------------------
    # dev profile: SMTP against mailpit (already in the dev compose
    # stack). "resend" swaps in the managed-API adapter for prod-like
    # profiles — resend_api_key comes from the SOPS bundle there, never
    # a committed default (empty string is not a usable key).
    email_provider: Literal["smtp", "resend"] = "smtp"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    email_sender: str = "noreply@aether.local"
    resend_api_key: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached, frozen)."""
    return Settings()

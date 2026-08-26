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
    # Optional overlap pair (S10 #109, §7.4/§7.6's "kid overlap" rotation
    # runbook): set only during an active rotation window, never used to
    # issue new tokens — verify-only, so already-issued access tokens
    # signed under the previous key keep validating until they naturally
    # expire, instead of every session breaking the instant the signing
    # key rotates. Unset (None) outside a rotation window — the default,
    # single-key posture ADR-7.2 already documents.
    jwt_previous_signing_key: str | None = None
    jwt_previous_kid: str | None = None
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

    # --- LLM Router (Sprint 4, ADR-3.5) ---------------------------------
    # Empty string is not a usable key — same posture as resend_api_key:
    # real values come from the SOPS bundle (ADR-7.5), never committed.
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # --- Usage metering & budgets (Sprint 4, §3.2.14) --------------------
    # cost_microcents units throughout: 1 microcent = 1e-6 US cent =
    # 1e-8 USD. $50 = 5_000_000_000 microcents (NFR-C-1's global demo
    # cap); $5/workspace/month is a conservative per-tenant default under
    # that, provisioned automatically at workspace creation.
    global_monthly_budget_microcents: int = 5_000_000_000
    default_workspace_monthly_budget_microcents: int = 500_000_000
    default_budget_soft_pct: int = 80
    # Pre-request ceiling estimate (§3.2.14): local prompt-token count +
    # max_tokens, priced at a conservative worst-case rate so the
    # provider-agnostic orchestrator never needs a capability registry of
    # its own. 60_000 = the more expensive of the two configured
    # providers' cost_per_1k_completion_microcents (openai/completion.py's
    # gpt-4o-mini) — update this if a pricier model chain is added.
    admission_ceiling_cost_per_1k_microcents: int = 60_000
    router_max_tokens: int = 1024
    # Bounds concurrent in-flight generations against any one provider
    # (issue #36): one tenant's burst must not saturate a shared
    # provider connection pool for every other tenant.
    router_max_concurrent_per_provider: int = 4

    # --- Object storage (Sprint 5, §3.2.13, ADR-3.8) --------------------
    # Dev-safe defaults matching MINIO_ROOT_USER/MINIO_ROOT_PASSWORD in
    # infra/compose/compose.yml — committed, non-secret, dev-profile-only
    # (same posture as POSTGRES_PASSWORD). Real values come from the
    # SOPS bundle in any non-dev profile.
    object_storage_endpoint: str = "localhost:9000"
    object_storage_access_key: str = "aether"
    object_storage_secret_key: str = "aether-dev-only"  # noqa: S105 — dev-only, not a real credential
    object_storage_secure: bool = False
    object_storage_bucket: str = "aether-documents"
    object_storage_presign_ttl_seconds: int = 900  # 15 min, ADR-3.8

    # --- Malware scanning (Sprint 5, §3.2.7, TB-6) -----------------------
    clamav_host: str = "localhost"
    clamav_port: int = 3310

    # --- Grounded chat refusal (Sprint 6/7, ADR-6.4) ------------------------
    # Gate 1's retrieval-score threshold — a real, data-derived calibrated
    # value (issue #73), not a guess: evals/harness/calibrate.py collected
    # the real fused RRF top-score for every "should ground" turn in the
    # v1 golden set (issue #70, 19 positive samples) against
    # LocalHashEmbeddingAdapter (embedding_version=1), then set the
    # threshold to 50% of the weakest observed positive match (0.0164),
    # comfortably clearing every real golden-set answer while staying
    # firmly above zero. ADR-6.4 is explicit that this must be
    # "calibrated per embedding model on the golden set" and
    # "recalibrated as part of the embedding migration procedure" — a
    # real provider embedding migration (or a v2 golden set with
    # off-topic-against-populated-KB negative cases, see
    # evals/golden/v1/README.md's documented gap) must rerun that script,
    # not reuse this number. A single global value is still an honest MVP
    # simplification (matching the schema's own "exactly one
    # embedding_version in play" posture, S5's chunks migration) — the
    # natural extension point once multiple embedding models/versions
    # coexist is a per-(model, version) mapping, not a bigger constant.
    retrieval_refusal_threshold: float = 0.0082

    # --- Observability (Sprint 9, NFR-O-1, §3.8) -------------------------
    # Empty string = tracing stays a no-op (same "falls back to an honest
    # local placeholder" posture as openai_api_key/resend_api_key above):
    # dev/CI without the observability compose profile running just gets
    # untraced spans, not import errors or a hung background exporter.
    otel_exporter_otlp_endpoint: str = ""
    otel_trace_sample_ratio: float = 0.10  # NFR-O-1: "sampled >= 10%"
    # The worker has no HTTP server of its own (a poll-loop daemon, not
    # a request handler) — prometheus_client's own tiny built-in server
    # (a background thread) is the standard way to expose /metrics for a
    # process shaped like this, same as any other Prometheus-instrumented
    # batch/daemon job.
    worker_metrics_port: int = 9090


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached, frozen)."""
    return Settings()

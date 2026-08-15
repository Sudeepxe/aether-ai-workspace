"""identity and tenancy schema

Revision ID: 37dc74e7cc34
Revises:
Create Date: 2026-08-14 04:16:01.298560

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

Creates the Sprint 1 slice of the §8.1 schema catalog: users, identities,
workspaces, memberships, refresh_tokens. Also creates the three runtime
DB roles from ADR-8.1 (app_api, app_worker, app_migrator) with distinct
grants, and enables forced row-level security on the one table in this
slice that actually carries a tenant (workspace) foreign key —
``memberships``. ``users``/``identities``/``refresh_tokens`` are scoped by
user, not by workspace, and ``workspaces`` *is* the tenant rather than
being tenant-scoped, so none of the four carry a ``workspace_id`` column
for RLS to key on — this matches the schema catalog's own "Identity &
tenancy (RLS-exempt or self-scoped)" classification. Future tenant-scoped
tables (threads, messages, documents, chunks — S3+) follow the identical
RLS pattern established here on ``memberships``.

Dev-only role passwords below mirror the existing committed-default
posture (``POSTGRES_PASSWORD=aether-dev-only`` etc., ADR-7.5): safe to
commit because they protect nothing outside the local dev compose
profile; production values are provisioned separately, out of band, and
are never committed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "37dc74e7cc34"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # --- extensions ------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # --- runtime roles (ADR-8.1: three roles, distinct grants) -----------
    # Idempotent: guards let this migration re-run cleanly against a DB
    # where a previous partial run already created a role.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_migrator') THEN
                CREATE ROLE app_migrator WITH LOGIN PASSWORD 'app-migrator-dev-only' BYPASSRLS;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_api') THEN
                CREATE ROLE app_api WITH LOGIN PASSWORD 'app-api-dev-only';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_worker') THEN
                CREATE ROLE app_worker WITH LOGIN PASSWORD 'app-worker-dev-only';
            END IF;
        END
        $$;
        """
    )

    # --- tables ------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE users (
            id UUID PRIMARY KEY,
            email CITEXT NOT NULL UNIQUE,
            password_hash TEXT,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE identities (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            provider_subject TEXT NOT NULL,
            email_verified BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (provider, provider_subject)
        )
        """
    )
    op.execute("CREATE INDEX identities_user_id_idx ON identities (user_id)")

    op.execute(
        """
        CREATE TABLE workspaces (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            model_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
        """
    )

    op.execute("CREATE TYPE membership_role AS ENUM ('owner', 'admin', 'member', 'viewer')")
    op.execute(
        """
        CREATE TABLE memberships (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role membership_role NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX memberships_user_id_idx ON memberships (user_id)")

    op.execute(
        """
        CREATE TABLE refresh_tokens (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            family_id UUID NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            device_fingerprint TEXT NOT NULL,
            used_at TIMESTAMPTZ,
            successor_id UUID REFERENCES refresh_tokens(id),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX refresh_tokens_family_id_idx ON refresh_tokens (family_id)")
    op.execute("CREATE INDEX refresh_tokens_user_id_idx ON refresh_tokens (user_id)")

    # --- row-level security (ADR-8.1) --------------------------------------
    # memberships is the only table in this migration that carries a
    # workspace_id (tenant) column — see module docstring.
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON memberships
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    # No SET LOCAL app.tenant_id in the session => current_setting(..., true)
    # returns '' => NULLIF makes it NULL => `workspace_id = NULL` is never
    # true for any row => zero rows visible, and INSERT/UPDATE with a
    # mismatched or absent tenant fails the WITH CHECK. Fails safe/closed
    # by construction, not by application-code discipline.

    # --- grants (least privilege per role, ADR-8.1) ------------------------
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users, identities TO app_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON workspaces, memberships TO app_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON refresh_tokens TO app_api")
    # app_worker: no grants on identity/tenancy tables in Sprint 1 — no
    # worker consumer touches auth data yet (ingestion/housekeeping land
    # S5+). Least privilege means granting nothing until something needs it.
    # app_migrator: owns DDL rights implicitly as the role that ran this
    # migration (in dev, migrations currently run via the bootstrap
    # `aether` role — see migrations/env.py and Settings.database_migrator_url
    # docstring); BYPASSRLS lets it operate across tenants for schema work
    # regardless of which role future deploy tooling runs migrations as.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_tokens")
    op.execute("DROP TABLE IF EXISTS memberships")
    op.execute("DROP TYPE IF EXISTS membership_role")
    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("DROP TABLE IF EXISTS identities")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP ROLE IF EXISTS app_worker")
    op.execute("DROP ROLE IF EXISTS app_api")
    op.execute("DROP ROLE IF EXISTS app_migrator")

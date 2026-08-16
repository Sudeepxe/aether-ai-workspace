"""documents and chunks schema

Revision ID: 20323f010125
Revises: 2fc1e0e07500
Create Date: 2026-08-16 01:33:21.328248

Expand-contract only (ADR-8.5): additive changes here; a later migration
does any contracting cleanup once N-1 compatibility is no longer needed.
No down-migrations run in production — ``downgrade()`` exists for local
dev/test only.

Sprint 5 slice of the §8.1 schema catalog (FR-KB-1/2/5):

- ``documents`` — one row per uploaded source file, status = an event
  projection of the ingestion pipeline (FR-KB-2's visible per-document
  progress: queued -> scanning -> parsing -> chunking -> embedding ->
  ready | failed). ``deleted_at`` is a soft-delete marker on the
  document itself; the chunks underneath it are hard-deleted (see
  ``chunks`` below and ADR-8.6) — FR-KB-5's "provable deletion" is about
  the actual content (chunks + vectors) being gone, not the document
  row's bookkeeping.
- ``chunks`` — the retrievable unit (ADR-6.2). ``embedding`` is
  nullable: a chunk row exists (for chunking-stage provenance/debugging)
  before the embedding stage has run. Vector column is plain
  ``vector(1536)`` (float32), not ``halfvec`` — ADR-8.4 explicitly gates
  the halfvec default on eval-harness recall verification, which
  doesn't exist until S7; adopting it now would violate the ADR's own
  conditional language. ``content_tsv`` is a generated column (the
  hybrid lexical leg, §3.2.5's RRF fusion, S6) so it's always in sync
  with ``content`` by construction, never hand-maintained.
- HNSW on ``embedding`` and GIN on ``content_tsv`` are plain (not
  partial-per-version) for this sprint: there is exactly one
  ``embedding_version`` in play until a real model migration happens.
  §8's "partial index per version during migrations" guidance is the
  adopted pattern *for that event*, not a thing to build speculatively
  now — RLS is what enforces workspace scoping at query time regardless
  of index shape.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20323f010125"
down_revision: str | None = "2fc1e0e07500"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- documents (FR-KB-1/2/5, §8.1) ---------------------------------------
    op.execute(
        """
        CREATE TYPE document_status AS ENUM (
            'queued', 'scanning', 'parsing', 'chunking', 'embedding', 'ready', 'failed'
        )
        """
    )
    op.execute(
        """
        CREATE TABLE documents (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            mime TEXT NOT NULL,
            size_bytes BIGINT NOT NULL,
            object_key TEXT NOT NULL,
            status document_status NOT NULL DEFAULT 'queued',
            failure_stage TEXT,
            failure_reason TEXT,
            version INT NOT NULL DEFAULT 1,
            superseded_by UUID REFERENCES documents(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ,
            CHECK (size_bytes > 0 AND size_bytes <= 52428800),  -- 50MB cap, FR-KB-1
            CHECK ((status = 'failed') = (failure_stage IS NOT NULL))
        )
        """
    )
    # The "what's processing" view (§8's index guidance): partial on
    # non-terminal statuses, so it stays small regardless of how many
    # documents a workspace accumulates over time.
    op.execute(
        "CREATE INDEX documents_workspace_status_idx ON documents (workspace_id, status) "
        "WHERE status NOT IN ('ready', 'failed')"
    )
    op.execute(
        "CREATE INDEX documents_workspace_created_idx ON documents (workspace_id, created_at DESC, id) "
        "WHERE deleted_at IS NULL"
    )

    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON documents
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # app_api owns document lifecycle (create on upload-confirm, soft-
    # delete via deleted_at) but never advances pipeline status — that's
    # the worker's job, so app_api gets no direct route to fake a
    # document into 'ready' without the pipeline actually running.
    op.execute("GRANT SELECT, INSERT, UPDATE ON documents TO app_api")
    # app_worker advances status through the pipeline but never creates
    # or removes a document row — upload/delete are user-initiated,
    # API-plane actions.
    op.execute("GRANT SELECT, UPDATE ON documents TO app_worker")

    # --- chunks (ADR-6.2, §8.1) -----------------------------------------------
    op.execute(
        """
        CREATE TABLE chunks (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            section_path TEXT NOT NULL,
            page_start INT,
            page_end INT,
            char_start INT NOT NULL,
            char_end INT NOT NULL,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            token_count INT NOT NULL,
            embedding vector(1536),
            embedding_model TEXT,
            embedding_version INT,
            content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (char_end > char_start),
            CHECK (page_end IS NULL OR page_start IS NULL OR page_end >= page_start),
            CHECK ((embedding IS NULL) = (embedding_model IS NULL)),
            CHECK ((embedding IS NULL) = (embedding_version IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX chunks_document_idx ON chunks (document_id)")
    op.execute(
        "CREATE INDEX chunks_embedding_hnsw_idx ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX chunks_content_tsv_gin_idx ON chunks USING gin (content_tsv)")

    op.execute("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON chunks
            USING (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (workspace_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # app_api never writes chunk content (that's the worker's job) but
    # does need DELETE for FR-KB-5's provable deletion — a document
    # delete request removes its chunks in the same transaction.
    op.execute("GRANT SELECT, DELETE ON chunks TO app_api")
    # app_worker creates chunk rows during chunking and fills in their
    # embedding during the embedding stage — no DELETE: deletion is a
    # user-initiated, API-plane action.
    op.execute("GRANT SELECT, INSERT, UPDATE ON chunks TO app_worker")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunks")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TYPE IF EXISTS document_status")

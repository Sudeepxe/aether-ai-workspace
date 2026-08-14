# ADR-3.2: pgvector in Postgres, with a Qdrant escape hatch on pre-committed triggers (D3-2)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Resolves Chapter 2's open question about the vector store: where do vectors live at the 1M-chunks-per-tenant target, given the vectors-are-derived-data DR posture (ADR-2.3) and the provable-deletion requirement (FR-KB-5)?

## Decision

pgvector inside PostgreSQL for v1, with an HNSW index and a namespace-per-tenant partitioning scheme.

## Alternatives considered

- **Qdrant** — the best OSS dedicated option and the named escape hatch; rejected for v1 because it introduces dual-write consistency problems, a second stateful system, and a separate DR story.

- **Pinecone** — vendor lock-in, cost at rest, and it violates the local-first portability requirement.

- **Milvus/Weaviate** — operational heft (etcd, multiple components) disproportionate to the envelope.

- **OpenSearch/Elasticsearch kNN** — attractive because hybrid search is native, but rejected for the JVM heap operations burden; Postgres full-text search covers the lexical leg well enough at this scale.

## Consequences

Easier: provable deletion becomes a single transaction — chunks, vectors, and metadata delete atomically with FK cascades; row-level security applies to vectors directly (one isolation mechanism, not two); one backup/restore/DR story. Harder: every vector row must carry embedding_model and embedding_version to prevent silent cross-version search corruption, and the envelope ceiling is real, which is why escape-hatch triggers are pre-committed.

## Revisit trigger

Sustained p95 retrieval over 400ms after tuning; HNSW build memory interfering with OLTP; more than roughly 20M total vectors; or a need for GPU-accelerated indexing.

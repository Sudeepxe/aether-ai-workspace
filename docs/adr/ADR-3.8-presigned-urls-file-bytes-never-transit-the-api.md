# ADR-3.8: Presigned URLs; file bytes never transit the API

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Uploaded and downloaded file bytes (up to 50MB per file) need a transport path that does not burden the stateless API tier's compute and bandwidth, or couple large-file transfer to HTTP request timeouts.

## Decision

Object storage uses presigned URLs for upload and download, so file bytes never transit the API tier; the API only issues short-TTL (15 minute), single-operation, content-type-and-size-constrained presigned URLs.

## Alternatives considered

- **Proxying file bytes through the API** — the status quo this decision moves away from; the rationale (bandwidth bypasses compute, decoupling from HTTP timeouts) directly argues against routing large file transfers through the stateless API tier.

## Consequences

Easier: API tier bandwidth and compute are not spent on file transfer, and large uploads don't risk HTTP timeout coupling. Harder: presigned URL issuance itself becomes an abuse surface — mitigated by short TTL, single-operation scope, and content-type/size constraints, monitored via the presign issuance rate.

## Revisit trigger

None stated.

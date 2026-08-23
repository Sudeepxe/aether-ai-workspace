# Evals

The eval harness (Blueprint §6.4) — outside `apps/api/src/aether` on purpose:
it's a consumer of the product (drives the real, deployed HTTP surface),
not part of it.

- `harness/` — the runner (issue #69): golden-case schema, real ingestion
  of `corpora/` fixtures, a case runner driving the real chat/retrieval
  stack end to end, and mechanical metrics (citation precision/recall,
  refusal correctness, retrieval hit-rate — all real, no LLM judge needed).
  Faithfulness scoring (needs a real cross-family LLM judge) lands in
  issue #71; until a provider key is configured, it's reported as
  honestly `not_measured`, never a fabricated number.
- `corpora/` — synthetic, project-authored source documents ingested as
  golden-case fixtures.
- `golden/v1/` — the v1 golden case set (issue #70).

Run it: `cd apps/api && PYTHONPATH=../.. uv run python -m evals.harness.cli run`
against a running dev stack (`make dev`, migrated, with the bucket
provisioned via `make minio-setup`).

**Data policy (ADR-9.5, Ch. 9 F-2):** all eval documents are synthetic and
project-authored. No scraped, copyrighted, or personal content — a PII scan
over this tree runs in CI as a tripwire (issue #72).

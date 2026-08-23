"""The eval harness (Blueprint §6.4, ADR-6.4/6.5/9.5) — drives the real
deployed HTTP surface (real ingestion pipeline, real hybrid retrieval,
real Gate 1, real chat flow) against the golden set in ``evals/golden/``
and scores the result. Lives outside ``apps/api/src/aether`` on purpose:
it's a consumer of the product, not part of it, and import-linter's
layered-architecture contracts (root_package = "aether") don't apply
here — same relationship ``apps/api/tests`` already has to ``aether``.
"""

from __future__ import annotations

"""Single source of truth for which EmbeddingProviderPort implementation
is active. Previously duplicated verbatim as a private ``_build_embedder``
in both ``http/composition.py`` and ``workers/composition.py``, and
separately hardcoded to ``LocalHashEmbeddingAdapter`` in four places
across ``evals/harness/`` (``corpus.py``, ``calibrate.py``, and — before
Phase 5 — nowhere at query time at all, which meant an eval run's
ingestion-time and query-time embeddings could silently come from two
different adapters if a provider key were ever configured mid-change).
Every one of those call sites now calls ``build_embedder`` instead.

Deliberately not inside ``aether.app``/``aether.ports``/``aether.domain``
(the three layers import-linter's "layered architecture" contract
names) or inside ``aether.adapters`` itself: this module's whole job is
to import and construct concrete adapters, which both composition
roots' own docstrings already establish as composition-root work
("nothing imports adapters except the composition root" —
``http/composition.py``). It sits alongside the two composition roots
as a third, shared piece of that same work, not as a new architectural
layer — verified against all three import-linter contracts (see the
Task A commit's real ``lint-imports`` output).
"""

from __future__ import annotations

from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.openai.embedding import OpenAiEmbeddingAdapter
from aether.config import Settings
from aether.ports.embedding import EmbeddingProviderPort


def build_embedder(settings: Settings) -> EmbeddingProviderPort:
    """Real OpenAI embeddings only if ``AETHER_OPENAI_API_KEY`` is
    configured; ``LocalHashEmbeddingAdapter`` (real, honest, if
    non-semantic) otherwise — the same "empty string is not a usable
    key" fallback posture every other provider-backed port in this
    codebase already uses. The returned instance's own ``model``/
    ``embedding_version`` attributes (``EmbeddingProviderPort``'s own
    protocol members) are the honest, introspectable answer to "which
    embedder actually produced these numbers" — callers should read
    those off the returned object, never assume or hardcode a label."""
    if settings.openai_api_key:
        return OpenAiEmbeddingAdapter(api_key=settings.openai_api_key)
    return LocalHashEmbeddingAdapter()

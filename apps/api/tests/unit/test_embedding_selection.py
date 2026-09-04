"""build_embedder's selection rule (Task A follow-up to Phase 5/6,
docs/REMEDIATION_PLAN.md) — previously duplicated, untested, private
logic in both composition roots; now a single shared, directly testable
function."""

from __future__ import annotations

import pytest

from aether.adapters.local.hash_embedding import LocalHashEmbeddingAdapter
from aether.adapters.openai.embedding import OpenAiEmbeddingAdapter
from aether.config import Settings
from aether.embedding_selection import build_embedder

pytestmark = pytest.mark.unit


def test_no_key_selects_the_local_hash_adapter() -> None:
    embedder = build_embedder(Settings())
    assert isinstance(embedder, LocalHashEmbeddingAdapter)


def test_openai_key_selects_the_real_openai_adapter() -> None:
    embedder = build_embedder(Settings(openai_api_key="sk-test"))
    assert isinstance(embedder, OpenAiEmbeddingAdapter)


def test_selected_adapter_exposes_a_real_model_label_for_reporting() -> None:
    """Whatever build_embedder returns must be self-identifying via the
    port's own model/embedding_version attributes — this is what lets a
    caller report which embedder actually produced a result instead of
    hardcoding a label."""
    hash_embedder = build_embedder(Settings())
    assert hash_embedder.model == "local-hash-fallback"
    assert hash_embedder.embedding_version == 1

    openai_embedder = build_embedder(Settings(openai_api_key="sk-test"))
    assert openai_embedder.model == "text-embedding-3-small"
    assert openai_embedder.embedding_version == 1

from __future__ import annotations

import pytest

from aether.adapters.local.noop_query_rewrite import NoOpQueryRewriteAdapter

pytestmark = pytest.mark.unit


async def test_returns_the_raw_query_unchanged_regardless_of_history() -> None:
    adapter = NoOpQueryRewriteAdapter()

    result = await adapter.rewrite(history=[], raw_query="what about its pricing?")

    assert result == "what about its pricing?"

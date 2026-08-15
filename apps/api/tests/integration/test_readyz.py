"""readyz reports ok when its dependencies are actually reachable — the
counterpart to tests/unit/test_healthz.py's "degraded without lifespan" case.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aether.config import get_settings

pytestmark = pytest.mark.integration


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


async def test_readyz_reports_ok_with_real_dependencies(
    postgres_url: str, redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # postgres_url's fixture setup already ran migrations (creating the
    # app_api role and schema) before yielding — no separate db_pool
    # dependency needed here.
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/readyz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
    finally:
        get_settings.cache_clear()

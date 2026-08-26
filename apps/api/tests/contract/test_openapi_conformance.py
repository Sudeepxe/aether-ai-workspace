"""OpenAPI conformance via schemathesis, in-process against the real
ASGI app (S10 #107, ADR-9.2, §10.2/§10.5's "contract tests (OpenAPI
diff + schemathesis against the app in-process)").

Needs real Postgres/Redis, like the ``integration``/``security`` marks
— *in-process* here means "dispatched straight through the ASGI app
object, no separately-running server," not "no dependencies at all":
this app's own lifespan (``http/app.py``'s ``_lifespan``) eagerly opens
a real connection pool at startup (``asyncpg.create_pool(min_size=1)``),
and schemathesis's ASGI transport runs that lifespan for real, so a
schema built from a bare ``create_app()`` with no reachable database
fails on the very first dispatched request — not a workaround-shaped
problem, a real fact about how this app boots. The schema itself is
therefore built inside a pytest fixture (``schemathesis.from_pytest_fixture``,
its documented lazy-loading mechanism) rather than at module level, so
env vars pointing at a real testcontainers Postgres/Redis are set
first — the same ``app_client``-fixture pattern every other real-HTTP
integration test in this repo already uses.

Scope note: this app declares each route's *success* response model
plus FastAPI's automatic 422 validation-error schema, but not the full
Problem+JSON error-status catalog (401/403/404/409/429/...) per route
via an explicit ``responses=`` block — those are handled generically by
``problem_json.py``'s exception handlers, not documented per-operation.
Declaring every possible status per route is real, valuable future work
(a bigger, separate change touching every route file), so
``status_code_conformance`` is deliberately excluded here rather than
either silently disabled without comment or forced green by mass-
editing every route — an honest, scoped gap, not a hidden one. What
*is* checked here still has real value: no operation ever 500s
(``not_a_server_error``), declared success/422 response bodies actually
match their schema, and the API genuinely rejects malformed input
(``negative_data_rejection``) rather than silently accepting it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import redis.asyncio as redis_asyncio
import schemathesis
from schemathesis.checks import (
    content_type_conformance,
    negative_data_rejection,
    not_a_server_error,
    response_schema_conformance,
)

from aether.config import get_settings

pytestmark = pytest.mark.contract

# FastAPI/Pydantic v2 emit OpenAPI 3.1 (the current spec version) —
# schemathesis's 3.1 support is still marked experimental upstream, but
# real (not a hidden downgrade to a 3.0 reinterpretation, which would
# silently misvalidate the 3.1-specific nullable-field shape Pydantic
# v2 actually emits).
schemathesis.experimental.OPEN_API_3_1.enable()


def _as_app_api_url(bootstrap_url: str) -> str:
    _, rest = bootstrap_url.split("://", 1)
    _, hostpart = rest.split("@", 1)
    return f"postgresql://app_api:app-api-dev-only@{hostpart}"


@pytest.fixture()
def api_schema(
    postgres_url: str,
    redis_url: str,
    redis_client: redis_asyncio.Redis,  # unused directly: flush-on-teardown isolates rate-limit buckets
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[schemathesis.BaseSchema]:
    monkeypatch.setenv("AETHER_DATABASE_URL", _as_app_api_url(postgres_url))
    monkeypatch.setenv("AETHER_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        from aether.http.app import create_app

        app = create_app()
        yield schemathesis.openapi.from_asgi("/openapi.json", app)
    finally:
        get_settings.cache_clear()


schema = schemathesis.from_pytest_fixture("api_schema")


@schema.parametrize()
def test_api_conforms_to_its_published_schema(case: schemathesis.Case) -> None:
    case.call_and_validate(
        checks=(
            not_a_server_error,
            response_schema_conformance,
            content_type_conformance,
            negative_data_rejection,
        )
    )
